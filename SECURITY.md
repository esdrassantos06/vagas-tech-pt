# Política de Segurança

## Reportar uma vulnerabilidade

Não abras uma issue pública para vulnerabilidades. Usa a aba **Security ->
Report a vulnerability** (GitHub Private Vulnerability Reporting) deste
repositório. Resposta esperada em alguns dias úteis.

## Âmbito

- Injeção (SQL, SSRF nos webhooks, etc.)
- Fuga de segredos (tokens de bot, webhooks, credenciais SMTP)
- Falhas de autenticação ou de verificação das subscrições
- Abuso dos canais de notificação (envio não solicitado a terceiros)

## Fora de âmbito

- Falta de rate limiting ao nível da aplicação (é feito no proxy/Traefik)
- Scrapers que param por mudança de HTML numa fonte (tratado pelo canary)
