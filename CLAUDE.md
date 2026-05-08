# mail-sync — Contexte projet

## Objectif

Lambda Python qui synchronise 2 comptes free.fr vers Gmail, en remplacement de la fonctionnalité "Consulter d'autres comptes via POP3" que Gmail web supprime en 2026.

## Architecture

```
EventBridge Scheduler (toutes les 5 min)
        │
        ▼
    Lambda Python 3.12 (mail-sync)
        │
        ├── IMAP → imap.free.fr (fetch messages UNSEEN)
        │
        └── Gmail API → users.messages.import() (insert dans la mailbox)

SSM Parameter Store (SecureString)
  ├── /mail-sync/free1/user
  ├── /mail-sync/free1/password
  ├── /mail-sync/free2/user
  ├── /mail-sync/free2/password
  ├── /mail-sync/gmail/refresh_token
  ├── /mail-sync/gmail/client_id
  ├── /mail-sync/gmail/client_secret
  └── /mail-sync/gmail/target_user
```

## Choix techniques

- **IMAP** (pas POP3) sur free.fr : `imap.free.fr:993` SSL
- **Fetch IMAP** : `BODY.PEEK[]` (pas `RFC822`) pour ne pas auto-marquer `\Seen` avant confirmation d'import
- **Gestion d'état** : flags IMAP natifs — on fetch les `UNSEEN`, on flag `\Seen` uniquement après import Gmail réussi. Pas de DynamoDB, pas de curseur externe.
- **Injection Gmail** : `users.messages.import` avec `labelIds: ["INBOX"]` (sans ce label les messages arrivent dans All Mail uniquement)
- **Auth Gmail** : OAuth2 avec refresh_token stocké dans SSM. Le refresh_token a été obtenu via `google-auth-oauthlib` avec le scope `gmail.modify`
- **Secrets** : tous dans SSM Parameter Store (SecureString), jamais en dur dans le code ou le Terraform
- **SSM** : un seul appel `GetParameters` (pluriel) pour les 8 paramètres, mis en cache dans `_params` au niveau module — ne déclenche des appels KMS qu'au cold start, pas à chaque invocation (la Lambda tourne toutes les 5 min, le container reste chaud en permanence)
- **Dépendances** : aucune — stdlib Python uniquement (`imaplib`, `urllib`, `base64`, `json`). Pas de `requests`.
- **Runtime** : Python 3.12
- **Timeout Lambda** : 60s
- **Memory** : 128 MB

## Pourquoi pas le forwarding SMTP ?

Les protocoles SPF/DKIM/DMARC identifient mal les mails forwardés depuis un tiers vers Gmail → risque élevé de spam ou rejet silencieux. L'API `messages.import` contourne ce problème en insérant directement dans la mailbox.

## Infrastructure AWS

Tout est en **Terraform** avec state stocké dans S3. Les ressources déployées :

- `aws_iam_role` + `aws_iam_role_policy` : role Lambda (`mail-sync-lambda-role`)
- `aws_iam_role` + `aws_iam_role_policy` : role Scheduler (`scheduler-invoke-role`)
- `aws_lambda_function` : `mail-sync` (zip direct de `lambda/handler.py` via `archive_file`)
- `aws_scheduler_schedule` : toutes les 5 minutes
- `aws_cloudwatch_metric_alarm` : alerte sur les erreurs Lambda
- Les `aws_ssm_parameter` sont créés manuellement (les valeurs ne doivent pas passer par le state Terraform)

## State Terraform

Stocké dans S3 : `s3://mail-sync-tfstate-<account-id>/mail-sync/terraform.tfstate`

Le bucket est configuré via `backend.hcl` (gitignored). Pour initialiser :

```bash
terraform init -backend-config=backend.hcl
```

## Free tier

Tout est gratuit ($0/mois).

| Ressource | Usage estimé | Free tier |
|---|---|---|
| Lambda invocations | ~8 640/mois | 1M/mois |
| Lambda compute | ~8 640 GB-s/mois | 400K GB-s |
| EventBridge Scheduler | ~8 640/mois | 14M/mois |
| SSM Parameter Store | 8 params standard | Gratuit |
| KMS (via SSM) | < 500 appels/mois (cold starts uniquement) | 20K/mois |
| CloudWatch Logs | quelques KB/mois | 5 GB/mois |
| CloudWatch Alarm | 1 alarme | 10 gratuites |
| S3 (state Terraform) | quelques KB | 5 GB/mois |

## AWS CLI

Région : `eu-west-1` — utiliser le profil par défaut (pas de profil `mail-sync` dans cet environnement).

## Structure du projet

```
mail-sync/
├── CLAUDE.md
├── README.md
├── main.tf
├── variables.tf
├── outputs.tf
├── backend.hcl          # gitignored — contient le nom du bucket S3
└── lambda/
    ├── handler.py
    └── requirements.txt  # vide — stdlib uniquement
```

## Ce qui est fait

- [x] Projet GCP créé, Gmail API activée
- [x] OAuth2 credentials créés (Desktop app)
- [x] refresh_token obtenu via bootstrap local
- [x] SSM parameters créés manuellement via AWS CLI
- [x] IAM user `mail-sync-deployer` créé avec policy restrictive
- [x] Terraform déployé (Lambda, IAM, Scheduler, CloudWatch alarm)
- [x] State Terraform dans S3 (bucket versionné, privé, chiffré)
- [x] Test end-to-end validé
