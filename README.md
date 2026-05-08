# mail-sync

Lambda Python qui synchronise 2 comptes free.fr vers Gmail, en remplacement de la fonctionnalité "Consulter d'autres comptes via POP3" que Gmail supprime en 2026.

## Architecture

```
EventBridge Scheduler (toutes les 5 min)
        │
        ▼
    Lambda Python 3.12 (mail-sync)
        │
        ├── IMAP → imap.free.fr (fetch UNSEEN, flag \Seen après import)
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

## Prérequis

- AWS CLI configuré
- Terraform >= 1.4
- Projet GCP avec Gmail API activée et credentials OAuth2 (Desktop app)
- Comptes free.fr avec accès IMAP activé

## Mise en place

### 1. Paramètres SSM

Créer les paramètres manuellement (les valeurs ne passent pas par Terraform) :

```bash
aws ssm put-parameter --name /mail-sync/free1/user     --value 'xxx'  --type SecureString
aws ssm put-parameter --name /mail-sync/free1/password --value 'xxx'  --type SecureString
aws ssm put-parameter --name /mail-sync/free2/user     --value 'xxx'  --type SecureString
aws ssm put-parameter --name /mail-sync/free2/password --value 'xxx'  --type SecureString
aws ssm put-parameter --name /mail-sync/gmail/client_id      --value 'xxx' --type SecureString
aws ssm put-parameter --name /mail-sync/gmail/client_secret  --value 'xxx' --type SecureString
aws ssm put-parameter --name /mail-sync/gmail/refresh_token  --value 'xxx' --type SecureString
aws ssm put-parameter --name /mail-sync/gmail/target_user    --value 'xxx' --type SecureString
```

Le `refresh_token` s'obtient via le flow OAuth2 avec `google-auth-oauthlib` et le scope `gmail.modify`. Les `client_id` et `client_secret` sont dans le fichier `credentials.json` téléchargé depuis la console GCP.

### 2. Bucket S3 pour le state Terraform

```bash
aws s3api create-bucket \
  --bucket mail-sync-tfstate-<account-id> \
  --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1

aws s3api put-bucket-versioning \
  --bucket mail-sync-tfstate-<account-id> \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket mail-sync-tfstate-<account-id> \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

Mettre à jour le nom du bucket dans le bloc `backend "s3"` de `main.tf`.

### 3. Déploiement

Créer un fichier `backend.hcl` (gitignored) :

```hcl
bucket = "mail-sync-tfstate-<account-id>"
```

Puis :

```bash
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

## Coût AWS

Tout est dans le free tier.

| Ressource | Usage | Free tier |
|---|---|---|
| Lambda | ~8 640 invocations/mois | 1M/mois |
| Lambda compute | ~8 640 GB-s/mois | 400K GB-s |
| EventBridge Scheduler | ~8 640/mois | 14M/mois |
| SSM Parameter Store | 8 params standard | Gratuit |
| KMS (via SSM) | < 500 appels/mois (cold starts uniquement) | 20K/mois |
| CloudWatch Logs | quelques KB/mois | 5 GB/mois |
| CloudWatch Alarm | 1 alarme | 10 gratuites |
| S3 (state Terraform) | quelques KB | 5 GB/mois |

## Pourquoi pas le forwarding SMTP ?

SPF/DKIM/DMARC identifient mal les mails forwardés depuis un tiers vers Gmail → risque de spam ou rejet silencieux. L'API `messages.import` contourne ce problème en insérant directement dans la mailbox.
