# Contribuir

Obrigado pelo interesse. Este projeto segue TDD e tem gates automáticos no CI.

## Ambiente

```bash
docker compose up -d db
make install
make migrate
```

## Fluxo

1. Cria um branch a partir de `main`.
2. **Escreve os testes primeiro** e confirma que falham (RED).
3. Implementa o mínimo para passar (GREEN) e refatora com os testes verdes.
4. Antes do PR: `make lint typecheck test` têm de passar, com cobertura >= 80%.
5. Se mexeste na API, corre `make openapi` e inclui o diff.
6. Abre o PR. O CI corre lint, typecheck, testes com Postgres, drift do OpenAPI,
   um guarda de migrations e o build da imagem Docker. Tudo tem de ficar verde.

## Regras do código

- Código e comentários em inglês. Texto e docs podem ser em português.
- Só doc-comments (docstrings). Sem comentários de narração.
- Migrations: nunca `DROP TABLE` / `DROP COLUMN` / `TRUNCATE` sem revisão humana.
  Prefere `uv run alembic revision --autogenerate` e lê o ficheiro antes de aplicar.

## Scrapers partidos

Se uma fonte mudar o HTML, o parser levanta `ParseError` e o canary semanal abre
uma issue. Atualiza a fixture em `tests/fixtures/<fonte>/` com o HTML/RSS novo e
ajusta o parser até os testes de contrato passarem.
