#!/usr/bin/env python3
"""
verificar_post.py — Blog Vida Útil
Última etapa do workflow publicar_artigos.yml: para cada post criado nesta
semana (estado "aprovacao_atual" -> posts_criados), valida estrutura, cards,
links, capa e conformidade AdSense — conforme checklist do CLAUDE.md
("CHECKLIST OBRIGATÓRIO — Verificação Pós-Publicação"). Envia resumo dos
problemas encontrados via Telegram.
"""

import os
import re
import sys
from base64 import b64encode

import requests

from estado_sheets import ler_estado

WP_URL  = os.environ['WORDPRESS_URL'].rstrip('/')
WP_USER = os.environ['WORDPRESS_USER']
WP_PASS = os.environ['WORDPRESS_APP_PASSWORD']
TELEGRAM_TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']

WP_AUTH  = b64encode(f'{WP_USER}:{WP_PASS}'.encode()).decode()
HEADERS  = {'Authorization': f'Basic {WP_AUTH}'}


def verificar_post(post_id: int) -> list[str]:
    erros = []
    try:
        r = requests.get(
            f'{WP_URL}/wp-json/wp/v2/posts/{post_id}',
            headers=HEADERS, params={'context': 'edit'}, timeout=20,
        )
    except Exception as e:
        return [f'ERRO: falha ao buscar o post ({e})']
    if not r.ok:
        return [f'ERRO: não foi possível buscar o post (HTTP {r.status_code})']

    post = r.json()
    content = post.get('content', {}).get('raw', '') or post.get('content', {}).get('rendered', '')

    cards = re.findall(r'"asin":"(MLB[^"]+)"', content)
    card_count = len(re.findall(r'hostinger-affiliate-plugin', content)) // 3
    if card_count < 3:
        erros.append(f'ERRO: {card_count} card(s) (mínimo 3)')
    mlbs_unicos = list(set(cards))
    if len(mlbs_unicos) > 1:
        erros.append(f'ALERTA: múltiplos MLBs nos cards: {mlbs_unicos}')

    hrefs = re.findall(r"href=['\"]([^'\"]+)['\"]", content)
    for href in hrefs:
        if href in (WP_URL, f'{WP_URL}/', '/'):
            erros.append(f'ERRO: link apontando para homepage: {href}')
        elif href.startswith('http'):
            try:
                hr = requests.head(href, timeout=5, allow_redirects=True)
                if hr.status_code == 404:
                    erros.append(f'ERRO: link 404: {href}')
            except Exception:
                pass

    if not post.get('featured_media'):
        erros.append('ERRO: sem featured image')

    word_count = len(re.sub(r'<[^>]+>', '', content).split())
    if word_count < 1000:
        erros.append(f'ALERTA: artigo curto ({word_count} palavras, mínimo 1200)')

    if 'affiliate-disclosure' not in content and 'links de afiliados' not in content.lower():
        erros.append('ERRO ADSENSE: disclosure de afiliados ausente')

    if 'Desvantage' not in content and 'Contra' not in content and 'contras' not in content.lower():
        erros.append('ERRO ADSENSE: seção de Contras/Desvantagens ausente')

    if post.get('featured_media'):
        try:
            mr = requests.get(
                f'{WP_URL}/wp-json/wp/v2/media/{post["featured_media"]}',
                headers=HEADERS, timeout=15,
            )
            if mr.ok and not mr.json().get('alt_text'):
                erros.append(f'ALERTA ADSENSE: capa sem alt text (media {post["featured_media"]})')
        except Exception:
            pass

    return erros


def verificar_produto_wc(wc_id: int) -> list[str]:
    try:
        r = requests.get(f'{WP_URL}/wp-json/wc/v3/products/{wc_id}', headers=HEADERS, timeout=15)
    except Exception as e:
        return [f'ERRO: falha ao buscar produto WC {wc_id} ({e})']
    if not r.ok:
        return [f'ERRO: produto WC {wc_id} não encontrado (HTTP {r.status_code})']
    prod = r.json()
    if not prod.get('description') or len(prod['description'].strip()) < 50:
        return [f'ERRO ADSENSE: produto WC {wc_id} sem descrição (thin content)']
    return []


def enviar_telegram(texto: str):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        requests.post(url, json={
            'chat_id':                  TELEGRAM_CHAT_ID,
            'text':                     texto,
            'parse_mode':               'HTML',
            'disable_web_page_preview': True,
        }, timeout=15)
    except Exception as e:
        print(f'[WARN] Telegram: {e}')


def main():
    aprovacao = ler_estado('aprovacao_atual')
    if not aprovacao:
        print('[INFO] sem estado "aprovacao_atual" — nada a verificar')
        sys.exit(0)

    posts = aprovacao.get('posts_criados', [])
    if not posts:
        print('[INFO] posts_criados vazio — nada a verificar')
        sys.exit(0)

    linhas = []
    total_erros = 0
    for p in posts:
        post_id = p.get('post_id')
        if not post_id:
            continue
        erros = verificar_post(post_id)
        if p.get('wc_id'):
            erros += verificar_produto_wc(p['wc_id'])
        if erros:
            total_erros += len(erros)
            linhas.append(f'⚠️ <b>Post #{post_id}</b> — {p.get("titulo", "")[:40]}')
            linhas.extend(f'  • {e}' for e in erros)
        else:
            print(f'[OK] Post #{post_id} verificado — tudo OK')

    if linhas:
        msg = '<b>🔍 Verificação pós-publicação</b>\n\n' + '\n'.join(linhas)
        enviar_telegram(msg[:4000])
    else:
        print('[OK] Todos os posts verificados sem problemas')

    print(f'\n[OK] verificação concluída — {total_erros} problema(s) encontrado(s)')


if __name__ == '__main__':
    main()
