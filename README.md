# vagas-tech-pt

![vagas-tech-pt](docs/assets/vagas-tech-pt.png)

Agregador open source de vagas tech em Portugal. Faz scraping de várias fontes,
normaliza e deduplica as vagas, expõe uma API REST documentada em OpenAPI, e
envia alertas por email, Discord ou Telegram.

A fragilidade dos scrapers (os sites mudam o HTML) é tratada como parte do
desenho: parsers falham alto com `ParseError`, cada execução fica registada em
`scrape_runs`, e um canary semanal avisa quando uma fonte parte.

## Stack

- Python 3.15, [uv](https://docs.astral.sh/uv/)
- FastAPI + Pydantic v2 (Swagger em `/docs`, OpenAPI em `/openapi.json`)
- PostgreSQL 18, SQLAlchemy 2 + Alembic (psycopg 3)
- Parsel (HTML/XPath) e Playwright + playwright-stealth
- pytest, ruff, mypy (strict)

## Fontes

- **net-empregos** (RSS, filtra vagas tech por tags)
- **itjobs.pt** (HTML)
- **LinkedIn** (páginas públicas de jobs via browser)

## Começar

```bash
cp .env.example .env        # ajusta DATABASE_URL e os canais de alerta
docker compose up -d db     # Postgres 18 local
make install                # uv sync + playwright chromium
make migrate                # aplica as migrations
make run                    # API em http://localhost:8000/docs
```

## Comandos

```bash
make test         # pytest + cobertura (>= 80%)
make lint         # ruff check + format --check
make typecheck    # mypy strict
make openapi      # regenera docs/openapi.json

python -m ptjobs scrape            # faz scraping de todas as fontes
python -m ptjobs scrape --source itjobs
python -m ptjobs notify            # envia os digests às subscrições verificadas
python -m ptjobs run               # scrape + notify (usado pela Scheduled Task)
```

## API

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/health` | Estado da aplicação e da base de dados |
| GET | `/jobs` | Lista vagas ativas, com filtros e paginação |
| GET | `/jobs/{id}` | Detalhe de uma vaga, com `also_on` (outras fontes) |
| GET | `/sources` | Última execução por fonte (monitorização) |
| POST | `/subscriptions` | Cria uma subscrição de alertas (opt-in) |
| GET | `/subscriptions/verify` | Confirma a subscrição |
| GET/PATCH/DELETE | `/subscriptions/me` | Gere a subscrição (Bearer manage token) |
| POST | `/telegram/webhook` | Webhook do bot de Telegram |

Filtros de `/jobs`: `q`, `city`, `work_mode`, `tag`, `min_salary`, `source`,
`since`, `page`, `size`.

## Deploy

A imagem publica em `ghcr.io/esdrassantos06/vagas-tech-pt` a cada push em `main`.
Em Coolify: Postgres 18 (one-click), a app a partir da imagem GHCR (o entrypoint
corre `alembic upgrade head` antes do `uvicorn`), uma Scheduled Task
`python -m ptjobs run` (cron `0 */2 * * *`), e rate limit em `/subscriptions`
via Traefik. Define `PUBLIC_BASE_URL` com `https` em produção.

## Desenvolvimento

O projeto segue TDD: os testes vêm primeiro (RED), depois a implementação
(GREEN), com a evidência em [docs/testing](docs/testing). Vê
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licença

[MIT](LICENSE).
