#!/usr/bin/env python3
"""
sugestao_semanal.py — Blog Vida Útil
Consulta produtos Casa Inteligente via endpoint autenticado do plugin
Hostinger Affiliate (OAuth com o Mercado Livre já configurado no WP — a API
pública do ML retorna 403 para chamadas sem token próprio) e envia top 7 via
Telegram para aprovação semanal.
Roda todo domingo às 18h BRT via GitHub Actions.
"""

import html as html_lib
import os
import re
import time
import requests
from base64 import b64encode
from datetime import datetime

from estado_sheets import salvar_estado

TELEGRAM_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
ML_PUBLISHER_ID = os.environ.get('ML_PUBLISHER_ID', '65450483')
ML_TRACKING_WORD = os.environ.get('ML_TRACKING_WORD', 'casalemaro')

# A API pública do ML (api.mercadolibre.com/sites/MLB/search) retorna 403
# Forbidden para qualquer chamada sem token OAuth próprio — confirmado em
# 15/06/2026 de múltiplas redes (ver memória pipeline_sugestao_semanal_bloqueios,
# item 2). A busca de produtos passou a usar o endpoint autenticado do plugin
# Hostinger Affiliate, que já tem OAuth com o Mercado Livre configurado.
WP_URL  = os.environ['WORDPRESS_URL'].rstrip('/')
WP_USER = os.environ['WORDPRESS_USER']
WP_PASS = os.environ['WORDPRESS_APP_PASSWORD']

WP_AUTH    = b64encode(f'{WP_USER}:{WP_PASS}'.encode()).decode()
WP_HEADERS = {'Authorization': f'Basic {WP_AUTH}', 'Content-Type': 'application/json'}

# Estrutura do HTML retornado por /hostinger-affiliate-plugin/v1/search-items
# (confirmado via teste real em 15/06/2026 — cada produto vem como um bloco):
# <div class="product-search-modal__item-result" title="..." data-asin="MLBxxxx"
#  data-image-url="..." data-title-shortened="..." data-url="...">
#   ... <div class="...-rating-label">5 <span>(9 reviews)</span></div>  (opcional)
#   ... <div class="...-price">R$ 109,99</div>                          (sempre presente)
ITEM_BLOCK_RE = re.compile(
    r'<div class="product-search-modal__item-result"\s*'
    r'title="(?P<title>.*?)"\s*'
    r'data-asin="(?P<asin>MLB\d+)"\s*'
    r'data-image-url="(?P<image>[^"]*)"\s*'
    r'data-title-shortened="[^"]*"\s*'
    r'data-url="(?P<url>[^"]*)"',
    re.S,
)
PRICE_RE  = re.compile(r'item-result-price">\s*R\$\s*([\d.,]+)')
RATING_RE = re.compile(r'item-result-rating-label">\s*([\d.,]+)\s*<span>\s*\((\d+)\s*reviews?\)', re.S)

LIMITE_POR_BUSCA = 25  # trim por busca, mesmo padrão do limite anterior da API ML

# Queries cobrindo todo o nicho Casa Inteligente
BUSCAS = [
    'tomada inteligente wifi',
    'lâmpada smart wifi',
    'câmera ip wifi',
    'fechadura digital wifi',
    'interruptor inteligente wifi',
    'robô aspirador smart',
    'echo dot alexa',
    'sensor movimento wifi tuya',
    'dimmer inteligente wifi',
    'smart plug wifi',
    'hub zigbee',
    'câmera segurança wifi externa',
    'controle universal wifi',
    'sensor porta janela wifi tuya',
]

PRECO_MIN = 25.0   # rebaixado de 40 → cobre smart plugs e sensores baratos
PRECO_MAX = 2500.0
# NOTA: o endpoint do plugin não expõe sold_quantity nem ordenação por vendas
# — a ordem do HTML retornado é a relevância padrão de busca do ML. Sem esse
# campo, manter VENDAS_MIN=0 e usar posição + rating/review_count como proxy.
VENDAS_MIN = 0

# MLBs já publicados no blog — atualizar a cada novo artigo publicado
MLB_PUBLICADOS = {
    'MLB63436648', 'MLB66838326', 'MLB67656602', 'MLB27618585',
    'MLB30020878', 'MLB47414628', 'MLB65590853', 'MLB54067306',
    'MLB28258210', 'MLB68907327', 'MLB27190731', 'MLB20751943',
    'MLB53926333', 'MLB47329913', 'MLB36862967', 'MLB52027865',
    'MLB68263881', 'MLB66850792', 'MLB25876045', 'MLB28368278',
    'MLB35966954', 'MLB58290930', 'MLB44981076', 'MLB23163117',
    'MLB29503401', 'MLB43918941', 'MLB54284933', 'MLB22696064',
    'MLB34967575', 'MLB24638981', 'MLB51474206', 'MLB6149192854',
    'MLB53818381',
}


def parse_preco_brl(texto: str) -> float:
    """Converte 'R$ 1.234,56' (já sem o prefixo) para float 1234.56."""
    try:
        return float(texto.replace('.', '').replace(',', '.'))
    except ValueError:
        return 0.0


def parse_items_html(html_blob: str) -> list:
    """Extrai cada produto do HTML do modal de busca do plugin Hostinger."""
    itens = []
    blocos = re.split(r'(?=<div class="product-search-modal__item-result")', html_blob)
    for bloco in blocos:
        m = ITEM_BLOCK_RE.search(bloco)
        if not m:
            continue
        preco_m  = PRICE_RE.search(bloco)
        rating_m = RATING_RE.search(bloco)
        itens.append({
            'id':           m.group('asin'),
            'title':        html_lib.unescape(m.group('title')),
            'price':        parse_preco_brl(preco_m.group(1)) if preco_m else 0.0,
            'image_url':    m.group('image'),
            'permalink':    m.group('url'),
            'rating':       float(rating_m.group(1).replace(',', '.')) if rating_m else 0.0,
            'review_count': int(rating_m.group(2)) if rating_m else 0,
        })
    return itens


def buscar_ml(query: str) -> list:
    """
    Busca produtos via endpoint autenticado do plugin Hostinger Affiliate
    (OAuth do plugin com o Mercado Livre — contorna o 403 da API pública do ML).
    """
    url = f'{WP_URL}/wp-json/hostinger-affiliate-plugin/v1/search-items'
    payload = {'keyword': query, 'marketplace': 'mercado'}
    for tentativa in range(3):
        try:
            r = requests.post(url, headers=WP_HEADERS, json=payload, timeout=30)
            if not r.ok:
                # Corpo da resposta ajuda a diferenciar bloqueio de firewall/WAF
                # (página HTML genérica) de rejeição do próprio plugin (JSON de erro)
                corpo = r.text[:300].replace('\n', ' ')
                print(f'[WARN] tentativa {tentativa + 1} falhou para "{query}": '
                      f'HTTP {r.status_code} — corpo: {corpo}')
                time.sleep(2 ** tentativa)
                continue
            html_blob = r.json().get('data', {}).get('html', '')
            return parse_items_html(html_blob)[:LIMITE_POR_BUSCA]
        except Exception as e:
            print(f'[WARN] tentativa {tentativa + 1} falhou para "{query}": {e}')
            time.sleep(2 ** tentativa)
    return []


def score(item: dict) -> float:
    """
    Score de relevância: base em posição na busca (índice invertido) com bônus
    para faixa de preço ideal (R$80-600) e produtos com avaliação consistente.
    O endpoint do plugin não expõe sold_quantity nem frete grátis — rating e
    review_count (quando presentes) servem como proxy de popularidade real.
    """
    posicao = item.get('_posicao', 999)
    s = max(0, 100 - posicao)  # posição 0 = score 100, posição 99 = score 1

    preco = item.get('price') or 0
    if 80 <= preco <= 600:
        s *= 1.3
    elif preco > 1000:
        s *= 0.8

    if item.get('review_count', 0) >= 5 and item.get('rating', 0) >= 4.0:
        s *= 1.15
    return s


def elegivel(item: dict) -> bool:
    """Filtra produto: não publicado e dentro da faixa de preço."""
    iid = item.get('id', '')
    preco = item.get('price') or 0
    return (
        iid not in MLB_PUBLICADOS
        and PRECO_MIN <= preco <= PRECO_MAX
    )


def formatar_preco(v: float) -> str:
    """Formata R$ 1.234,56"""
    return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def montar_mensagem(top7: list, data_semana: str) -> str:
    linhas = [
        '<b>🏠 Sugestão Semanal — Casa Inteligente</b>',
        f'<b>📅 Semana de {data_semana}</b>',
        '',
        'Responda <b>"aprovado"</b> para usar todos,',
        'ou informe os MLB IDs que quer excluir.',
        '',
    ]
    for i, item in enumerate(top7, 1):
        iid = item['id']
        nome = item['title']
        if len(nome) > 55:
            nome = nome[:52] + '...'
        preco = formatar_preco(item.get('price') or 0)
        link = item.get('permalink', f'https://produto.mercadolivre.com.br/{iid}')
        avaliacao = ''
        if item.get('review_count', 0) > 0:
            avaliacao = f' · ⭐ {item["rating"]:.1f} ({item["review_count"]} avaliações)'

        linhas.append(
            f'<b>{i}. {nome}</b>\n'
            f'💰 {preco}{avaliacao}\n'
            f'🔗 <a href="{link}">{iid}</a>\n'
            f'<code>{iid}</code>'
        )
        linhas.append('')

    linhas.append('<i>Gerado automaticamente — Blog Vida Útil</i>')
    return '\n'.join(linhas)


def enviar_telegram(texto: str):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': texto,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    msg_id = r.json()['result']['message_id']
    print(f'[OK] Telegram message_id={msg_id}')


def main():
    print(f'[INFO] {datetime.now().isoformat()} — sugestão semanal iniciada')

    vistos: set = set()
    candidatos: list = []

    for query in BUSCAS:
        print(f'[INFO] buscando: {query}')
        items = buscar_ml(query)
        novos = 0
        for posicao, item in enumerate(items):
            iid = item.get('id', '')
            if iid in vistos:
                continue
            vistos.add(iid)
            item['_posicao'] = posicao  # usado no score
            if elegivel(item):
                item['_score'] = score(item)
                candidatos.append(item)
                novos += 1
        print(f'       → {novos} novos elegíveis (acumulado: {len(candidatos)})')
        time.sleep(1.2)  # evita sobrecarregar o WP (hosting compartilhado) entre buscas

    print(f'[INFO] {len(candidatos)} candidatos totais após filtros')

    if not candidatos:
        enviar_telegram(
            '⚠️ <b>Sugestão Semanal</b>\n\n'
            'Nenhum produto novo encontrado esta semana.\n'
            'Verifique os filtros ou amplie as buscas no script.'
        )
        return

    candidatos.sort(key=lambda x: x['_score'], reverse=True)
    top7 = candidatos[:7]

    data_semana = datetime.now().strftime('%d/%m/%Y')
    mensagem = montar_mensagem(top7, data_semana)
    enviar_telegram(mensagem)

    # Detalhes (MLB, título, score) ficam só no Telegram — não em log público do Actions
    print(f'[OK] Top {len(top7)} enviados via Telegram')

    # Salva estado para o detector de aprovação — planilha Google Sheets, não
    # arquivo no repositório (evita comitar título/MLB no histórico Git público)
    sugestao = {
        'data': datetime.now().isoformat(),
        'semana': data_semana,
        'mlbs': [item['id'] for item in top7],
        'processado': False,
    }
    salvar_estado('ultima_sugestao', sugestao)
    print('[OK] estado "ultima_sugestao" salvo na planilha (aba estado_pipeline)')


if __name__ == '__main__':
    main()
