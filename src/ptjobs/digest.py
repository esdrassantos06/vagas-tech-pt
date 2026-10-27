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


def _email_shell(title: str, body: str, footer: str) -> str:
    return f"""<!doctype html>
<html lang="pt"><body style="margin:0;background:#0b0f14;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f14;padding:24px 12px">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#121821;border:1px solid #243042;border-radius:14px">
<tr><td style="padding:24px 28px;border-bottom:1px solid #243042">
<span style="font-size:20px;font-weight:800;color:#e6edf3">vagas-tech-<span style="color:{ACCENT}">pt</span></span>
<div style="font-size:15px;color:#8b98a9;margin-top:6px">{escape(title)}</div>
</td></tr>
<tr><td style="padding:12px 28px 24px">{body}</td></tr>
<tr><td style="padding:18px 28px;border-top:1px solid #243042;font-size:12px;line-height:1.6;color:#8b98a9">{footer}</td></tr>
</table></td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (
        f'<a href="{escape(url)}" style="display:inline-block;background:{ACCENT};'
        "color:#0b0f14;font-weight:700;text-decoration:none;padding:12px 20px;"
        f'border-radius:10px;font-size:14px">{escape(label)}</a>'
    )


def render_email_html(digest: Digest) -> str:
    """HTML email digest with one card per job."""
    cards = "".join(
        '<div style="padding:16px 0;border-bottom:1px solid #243042">'
        f'<a href="{escape(job.url)}" style="font-size:16px;font-weight:700;'
        f'color:#e6edf3;text-decoration:none">{escape(job.title)}</a>'
        f'<div style="font-size:13px;color:#8b98a9;margin-top:4px">'
        f"{escape(_meta(job))}</div>"
        + (
            '<div style="margin-top:8px">'
            + "".join(
                '<span style="display:inline-block;font-size:11px;color:#e6edf3;'
                "background:#16202c;border:1px solid #243042;border-radius:6px;"
                f'padding:2px 8px;margin:0 6px 6px 0">{escape(tag)}</span>'
                for tag in job.tags[:6]
            )
            + "</div>"
            if job.tags
            else ""
        )
        + "</div>"
        for job in digest.jobs
    )
    more = (
        f'<p style="color:#8b98a9;font-size:14px">... e mais {digest.remaining} '
        "vagas.</p>"
        if digest.remaining
        else ""
    )
    body = f'{cards}{more}<p style="margin-top:22px">{_button(digest.jobs_url, "Ver todas as vagas")}</p>'
    footer = (
        "Recebes este email porque ativaste alertas no vagas-tech-pt. "
        f'<a href="{escape(digest.unsubscribe_url)}" style="color:{ACCENT}">'
        "Cancelar alertas</a>."
    )
    return _email_shell(_headline(digest), body, footer)


def render_verification_email(link: str) -> tuple[str, str]:
    """Plain-text and HTML bodies of the opt-in confirmation email."""
    text = f"Confirma a tua subscrição de alertas de vagas tech neste link: {link}"
    body = (
        '<p style="color:#e6edf3;font-size:15px;line-height:1.6">Falta só um passo '
        "para começares a receber as novas vagas tech que batem com os teus "
        f'filtros.</p><p style="margin-top:22px">{_button(link, "Confirmar subscrição")}</p>'
    )
    footer = "Se não pediste estes alertas, ignora este email."
    return text, _email_shell("Confirma a tua subscrição", body, footer)


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
        '<p style="color:#e6edf3;font-size:15px;line-height:1.6">O teu novo token '
        "de gestão (o anterior deixou de funcionar):</p>"
        '<p style="font-family:ui-monospace,Menlo,monospace;font-size:14px;'
        "color:#e6edf3;background:#16202c;border:1px solid #243042;"
        f'border-radius:8px;padding:12px;word-break:break-all">{escape(token)}</p>'
        f'<p style="margin-top:22px">{_button(docs_url, "Abrir a API")}</p>'
    )
    footer = (
        "Se não pediste um novo token, podes ignorar este email. "
        f'<a href="{escape(unsubscribe_url)}" style="color:{ACCENT}">'
        "Cancelar alertas</a>."
    )
    return text, _email_shell("Novo token de gestão", body, footer)


def render_page(title: str, message: str, *, confirm: bool = False) -> str:
    """Small standalone HTML page for links opened in a browser.

    With confirm=True it shows a button that POSTs back to the page's own URL.
    """
    form = (
        '<form method="post" style="margin-top:22px">'
        f'<button type="submit" style="background:{ACCENT};color:#0b0f14;border:0;'
        "font-weight:700;padding:12px 20px;border-radius:10px;font-size:15px;"
        'cursor:pointer">Confirmar</button></form>'
        if confirm
        else ""
    )
    return f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · vagas-tech-pt</title></head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0b0f14;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;padding:16px">
<main style="max-width:440px;width:100%;background:#121821;border:1px solid #243042;border-radius:14px;padding:28px">
<div style="font-size:18px;font-weight:800;color:#e6edf3">vagas-tech-<span style="color:{ACCENT}">pt</span></div>
<h1 style="font-size:22px;color:#e6edf3;margin:18px 0 8px">{escape(title)}</h1>
<p style="color:#8b98a9;font-size:15px;line-height:1.6;margin:0">{escape(message)}</p>{form}
</main></body></html>"""
