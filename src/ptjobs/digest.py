"""Digest and page rendering for each notification channel.

All user-facing alert texts live here. Scraped values are always HTML-escaped
before being placed in HTML (email, Telegram, web pages).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from ptjobs.models import Job

ACCENT = "#3ddc97"
TELEGRAM_MAX_CHARS = 4096
_TELEGRAM_FOOTER = "/filtros para ajustar · /parar para cancelar"
_SENIORITIES = {
    "intern": "Estágio",
    "junior": "Júnior",
    "mid": "Mid",
    "senior": "Sénior",
    "lead": "Lead",
}
_WORK_MODES = {"remote": "Remoto", "hybrid": "Híbrido", "onsite": "Presencial"}


@dataclass(frozen=True)
class Digest:
    """New matching jobs for one subscription, plus the links shown with them."""

    jobs: list[Job]
    remaining: int
    jobs_url: str
    unsubscribe_url: str


def _meta(job: Job) -> str:
    parts = [job.company]
    if job.seniority in _SENIORITIES:
        parts.append(_SENIORITIES[job.seniority])
    if job.city:
        parts.append(job.city.title())
    if job.work_mode in _WORK_MODES:
        parts.append(_WORK_MODES[job.work_mode])
    if job.salary_min and job.salary_max:
        low, high = job.salary_min // 1000, job.salary_max // 1000
        parts.append(f"€{low}k-{high}k" if low != high else f"€{low}k")
    return " · ".join(parts)


def _headline(digest: Digest) -> str:
    total = len(digest.jobs) + digest.remaining
    return f"{total} vaga nova" if total == 1 else f"{total} vagas novas"


def render_text(digest: Digest) -> str:
    """Plain-text digest (email fallback part)."""
    lines = [_headline(digest), ""]
    for job in digest.jobs:
        lines += [f"- {job.title}", f"  {_meta(job)}", f"  {job.url}", ""]
    if digest.remaining:
        lines += [f"... e mais {digest.remaining}: {digest.jobs_url}", ""]
    lines.append(f"Cancelar alertas: {digest.unsubscribe_url}")
    return "\n".join(lines)


def render_telegram(digest: Digest) -> str:
    """Telegram message using parse_mode=HTML, trimmed to the 4096-char limit.

    Jobs that do not fit are added to the "e mais N" count.
    """
    total = len(digest.jobs) + digest.remaining
    entries = [
        f'<b><a href="{escape(job.url)}">{escape(job.title)}</a></b>\n'
        f"{escape(_meta(job))}"
        for job in digest.jobs
    ]
    shown = len(entries)
    while True:
        rest = total - shown
        more = (
            [f'... e mais {rest}: <a href="{escape(digest.jobs_url)}">ver todas</a>']
            if rest
            else []
        )
        text = "\n\n".join(
            [f"<b>{_headline(digest)}</b>", *entries[:shown], *more, _TELEGRAM_FOOTER]
        )
        if len(text) <= TELEGRAM_MAX_CHARS or shown == 0:
            return text
        shown -= 1


def render_discord(digest: Digest) -> dict[str, Any]:
    """Discord webhook payload: one embed per job (max 10)."""
    links = f"[Ver todas as vagas]({digest.jobs_url})"
    if digest.remaining:
        links = f"... e mais {digest.remaining}. " + links
    return {
        "content": (
            f"**{_headline(digest)}**\n{links} · "
            f"[Cancelar alertas]({digest.unsubscribe_url})"
        ),
        "embeds": [
            {
                "title": job.title[:256],
                "url": job.url,
                "description": _meta(job)[:300],
                "color": int(ACCENT[1:], 16),
            }
            for job in digest.jobs[:10]
        ],
        "allowed_mentions": {"parse": []},
    }


def render_discord_verification(link: str) -> dict[str, Any]:
    """Discord webhook payload asking the channel to confirm the subscription."""
    return {
        "embeds": [
            {
                "title": "Confirma a subscrição de alertas",
                "url": link,
                "description": (
                    "Abre o link e carrega em **Confirmar** para começares a "
                    f"receber aqui as novas vagas tech.\n\n[Confirmar subscrição]({link})"
                ),
                "color": int(ACCENT[1:], 16),
            }
        ],
        "allowed_mentions": {"parse": []},
    }


_SOURCES = {
    "linkedin": "LinkedIn",
    "itjobs": "itjobs.pt",
    "net_empregos": "Net-Empregos",
}
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
_LIGHT = {
    "canvas": "#f2f5f3",
    "card": "#ffffff",
    "line": "#e2e8e4",
    "ink": "#0f1a15",
    "muted": "#4a5851",
    "faint": "#66736c",
    "link": "#0a7446",
    "chip": "#edf3ef",
}
_DARK_CSS = """
:root{color-scheme:light dark;supported-color-schemes:light dark}
@media (prefers-color-scheme: dark){
.bg{background:#0b0f14!important}
.card{background:#121821!important;border-color:#243042!important}
.line{border-color:#243042!important}
.ink{color:#e6edf3!important}
.muted{color:#a8b4c2!important}
.faint{color:#8b98a9!important}
.link{color:#3ddc97!important}
.chip{background:#16202c!important;color:#d3dce6!important}
}
@media (max-width:620px){.pad{padding-left:20px!important;padding-right:20px!important}}
"""


def _email_shell(preheader: str, title: str, body: str, footer: str) -> str:
    c = _LIGHT
    filler = "&#847;&zwnj;&nbsp;" * 60
    return f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<title>{escape(title)}</title><style>{_DARK_CSS}</style></head>
<body class="bg" style="margin:0;padding:0;background:{c["canvas"]};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}{filler}</div>
<table role="presentation" class="bg" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{c["canvas"]}" style="background:{c["canvas"]}">
<tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;font-family:{_FONT}">
<tr><td class="pad" style="padding:0 28px 16px;font-size:15px;font-weight:800;letter-spacing:-0.01em;color:{c["ink"]}"><span class="ink">vagas-tech-<span class="link" style="color:{c["link"]}">pt</span></span></td></tr>
<tr><td class="card" bgcolor="{c["card"]}" style="background:{c["card"]};border:1px solid {c["line"]};border-radius:14px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td class="pad" style="padding:30px 28px 6px">
<h1 class="ink" style="margin:0;font-size:26px;line-height:1.2;font-weight:800;letter-spacing:-0.02em;color:{c["ink"]}">{escape(title)}</h1>
</td></tr>
{body}
</table></td></tr>
<tr><td class="pad faint" style="padding:20px 28px 0;font-size:12px;line-height:1.6;color:{c["faint"]}">{footer}</td></tr>
</table></td></tr></table></body></html>"""


def _paragraph(html: str) -> str:
    return (
        f'<tr><td class="pad muted" style="padding:10px 28px 0;font-size:15px;'
        f'line-height:1.6;color:{_LIGHT["muted"]}">{html}</td></tr>'
    )


def _button(url: str, label: str) -> str:
    return f"""<tr><td class="pad" style="padding:24px 28px 30px">
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td bgcolor="{ACCENT}" style="border-radius:10px;background:{ACCENT}">
<a href="{escape(url)}" style="display:inline-block;padding:13px 22px;font-size:15px;font-weight:700;color:#06120c;text-decoration:none;border-radius:10px">{escape(label)}</a>
</td></tr></table></td></tr>"""


def _footer_link(url: str, label: str) -> str:
    return (
        f'<a class="link" href="{escape(url)}" style="color:{_LIGHT["link"]};'
        f'text-decoration:underline">{escape(label)}</a>'
    )


def _job_row(job: Job) -> str:
    c = _LIGHT
    details = [
        _SENIORITIES.get(job.seniority or ""),
        job.city.title() if job.city else None,
        _WORK_MODES.get(job.work_mode or ""),
    ]
    meta = f'<strong class="ink" style="font-weight:600;color:{c["ink"]}">{escape(job.company or "")}</strong>'
    meta += "".join(f" &middot; {escape(d)}" for d in details if d)
    salary = ""
    if job.salary_min and job.salary_max:
        low, high = job.salary_min // 1000, job.salary_max // 1000
        amount = f"€{low}k–{high}k" if low != high else f"€{low}k"
        salary = f' &middot; <span class="link" style="font-weight:600;white-space:nowrap;color:{c["link"]}">{amount}/ano</span>'
    chips = "".join(
        f'<span class="chip" style="display:inline-block;margin:0 6px 6px 0;padding:3px 9px;border-radius:6px;background:{c["chip"]};font-size:12px;line-height:1.4;color:#33413a">{escape(tag)}</span>'
        for tag in job.tags[:5]
    )
    source = _SOURCES.get(job.source or "")
    via = (
        f'<span class="faint" style="display:inline-block;margin:0 0 6px;font-size:12px;line-height:1.4;color:{c["faint"]}">via {source}</span>'
        if source
        else ""
    )
    return f"""<tr><td class="pad" style="padding:0 28px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td class="line" style="padding:18px 0 12px;border-top:1px solid {c["line"]}">
<a class="ink" href="{escape(job.url or "")}" style="font-size:17px;line-height:1.35;font-weight:700;color:{c["ink"]};text-decoration:none">{escape(job.title or "")}</a>
<div class="muted" style="margin-top:5px;font-size:14px;line-height:1.5;color:{c["muted"]}">{meta}{salary}</div>
<div style="margin-top:10px">{chips}{via}</div>
</td></tr></table></td></tr>"""


def render_email_html(digest: Digest) -> str:
    """HTML email digest: one row per job, light by default with a dark mode."""
    companies = list(dict.fromkeys(job.company for job in digest.jobs if job.company))
    preheader = f"{_headline(digest)}: {', '.join(companies[:2])} e mais."
    rows = "".join(_job_row(job) for job in digest.jobs)
    intro = _paragraph("Batem com os teus filtros. As mais recentes primeiro.")
    more = (
        _paragraph(f"Há mais {digest.remaining} vagas à tua espera no site.")
        if digest.remaining
        else ""
    )
    body = (
        f'{intro}<tr><td style="height:18px;line-height:18px;font-size:0">&nbsp;</td></tr>'
        f"{rows}{more}{_button(digest.jobs_url, 'Ver todas as vagas')}"
    )
    footer = (
        "Recebes este email porque ativaste alertas no vagas-tech-pt. "
        f"{_footer_link(digest.unsubscribe_url, 'Cancelar alertas')}"
    )
    return _email_shell(preheader, _headline(digest), body, footer)


def render_verification_email(link: str) -> tuple[str, str]:
    """Plain-text and HTML bodies of the opt-in confirmation email."""
    text = f"Confirma a tua subscrição de alertas de vagas tech neste link: {link}"
    body = (
        _paragraph(
            "Falta só um passo para começares a receber as novas vagas tech que "
            "batem com os teus filtros."
        )
        + _button(link, "Confirmar subscrição")
        + f'<tr><td class="pad faint" style="padding:0 28px 28px;font-size:12px;line-height:1.6;color:{_LIGHT["faint"]};word-break:break-all">'
        f"O botão não funciona? Abre este link: {_footer_link(link, link)}</td></tr>"
    )
    footer = (
        "Se não pediste estes alertas, ignora este email. Nada muda sem confirmares."
    )
    return text, _email_shell(
        "Confirma para começares a receber vagas tech.",
        "Confirma a tua subscrição",
        body,
        footer,
    )


def render_recovery_email(
    token: str, docs_url: str, unsubscribe_url: str
) -> tuple[str, str]:
    """Plain-text and HTML bodies of the manage-token recovery email."""
    text = (
        "O teu novo token de gestão (o anterior deixou de funcionar):\n\n"
        f"{token}\n\nUsa-o em {docs_url} como Authorization: Bearer <token>.\n"
        f"Cancelar alertas: {unsubscribe_url}"
    )
    body = (
        _paragraph("O anterior deixou de funcionar. Guarda este num sítio seguro.")
        + f'<tr><td class="pad" style="padding:16px 28px 0">'
        f'<div class="chip ink" style="padding:14px 16px;border-radius:10px;background:{_LIGHT["chip"]};font-family:{_MONO};font-size:14px;line-height:1.5;color:{_LIGHT["ink"]};word-break:break-all">{escape(token)}</div></td></tr>'
        + _paragraph(
            'Usa-o na API no cabeçalho <code style="font-family:'
            f'{_MONO};font-size:13px">Authorization: Bearer &lt;token&gt;</code>.'
        )
        + _button(docs_url, "Abrir a API")
    )
    footer = (
        "Se não pediste um novo token, podes ignorar este email. "
        f"{_footer_link(unsubscribe_url, 'Cancelar alertas')}"
    )
    return text, _email_shell(
        "O teu novo token de gestão está aqui.",
        "O teu novo token de gestão",
        body,
        footer,
    )


_PAGE_CSS = f"""
:root{{--canvas:{_LIGHT["canvas"]};--card:{_LIGHT["card"]};--line:{_LIGHT["line"]};--ink:{_LIGHT["ink"]};--muted:{_LIGHT["muted"]};--link:{_LIGHT["link"]};color-scheme:light dark}}
@media (prefers-color-scheme: dark){{:root{{--canvas:#0b0f14;--card:#121821;--line:#243042;--ink:#e6edf3;--muted:#a8b4c2;--link:#3ddc97}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;background:var(--canvas);color:var(--ink);font:16px/1.6 {_FONT}}}
::selection{{background:{ACCENT};color:#06120c}}
main{{width:100%;max-width:440px}}
.brand{{margin:0 0 14px 4px;font-size:15px;font-weight:800;letter-spacing:-0.01em}}
.brand span{{color:var(--link)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:30px 28px}}
h1{{margin:0 0 8px;font-size:24px;line-height:1.2;letter-spacing:-0.02em;text-wrap:balance}}
p{{margin:0;color:var(--muted)}}
button{{margin-top:24px;border:0;border-radius:10px;padding:13px 22px;background:{ACCENT};color:#06120c;font:700 15px/1 {_FONT};cursor:pointer;transition:filter .15s ease-out}}
button:hover{{filter:brightness(1.06)}}
button:active{{filter:brightness(.94)}}
button:focus-visible{{outline:2px solid var(--link);outline-offset:3px}}
"""


def render_page(title: str, message: str, *, confirm: bool = False) -> str:
    """Small standalone HTML page for links opened in a browser.

    With confirm=True it shows a button that POSTs back to the page's own URL.
    """
    form = (
        '<form method="post"><button type="submit">Confirmar</button></form>'
        if confirm
        else ""
    )
    return f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="robots" content="noindex">
<title>{escape(title)} · vagas-tech-pt</title><style>{_PAGE_CSS}</style></head>
<body><main><div class="brand">vagas-tech-<span>pt</span></div>
<div class="card"><h1>{escape(title)}</h1><p>{escape(message)}</p>{form}</div>
</main></body></html>"""
