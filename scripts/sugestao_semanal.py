#!/usr/bin/env python3
"""
sugestao_semanal.py — Blog Vida Útil
Consulta produtos trending Casa Inteligente no Mercado Livre
e envia top 7 via Telegram para aprovação semanal.
Roda todo domingo às 18h BRT via GitHub Actions.
"""

import json
import os
import time
import requests
from datetime import datetime

TELEGRAM_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
ML_PUBLISHER_ID = os.environ.get('ML_PUBLISHER_ID', '65450483')
ML_TRACKING_WORD = os.environ.get('ML_TRACKING_WORD', 'casalemaro')

ML_BASE = 'https://api.mercadolibre.com'
HEADERS = {'User-Agent': 'Blog-Vida-Util-Bot/1.0'}

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
# NOTA: sold_quantity NÃO é retornado na busca do ML — a API ordena por mais
# vendidos mas não devolve o campo. Filtrar por vendas mínimas eliminaria tudo.
# O sort=sold_quantity já garante que os primeiros resultados são os mais vendidos.
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


def buscar_ml(query: str, limit: int = 25) -> list:
    """Busca no ML ordenando por mais vendidos, com retry automático."""
    url = f'{ML_BASE}/sites/MLB/search'
    params = {
        'q': query,
        'sort': 'sold_quantity',
        'limit': limit,
        'condition': 'new',
    }
    for tentativa in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json().get('results', [])
        except Exception as e:
            print(f'[WARN] tentativa {tentativa + 1} falhou para "{query}": {e}')
            time.sleep(2 ** tentativa)
    return []


def score(item: dict) -> float:
    """
    Score de relevância: base em posição da busca (índice invertido) com bônus
    para faixa de preço ideal (R$80-600) e frete grátis.
    sold_quantity não é retornado na busca ML, então não podemos usá-lo diretamente.
    """
    # Posição na busca como proxy de popularidade (já ordenado por sold_quantity)
    posicao = item.get('_posicao', 999)
    s = max(0, 100 - posicao)  # posição 0 = score 100, posição 99 = score 1

    preco = item.get('price') or 0
    frete_gratis = item.get('shipping', {}).get('free_shipping', False)

    if 80 <= preco <= 600:
        s *= 1.3
    elif preco > 1000:
        s *= 0.8
    if frete_gratis:
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
        vendas = item.get('sold_quantity') or 0
        link = item.get('permalink', f'https://produto.mercadolivre.com.br/{iid}')
        frete = ' 🚚 Frete grátis' if item.get('shipping', {}).get('free_shipping') else ''
        vendas_str = f' · 📦 {vendas:,} vendidos' if vendas > 0 else ''

        linhas.append(
            f'<b>{i}. {nome}</b>\n'
            f'💰 {preco}{vendas_str}{frete}\n'
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
        time.sleep(1.2)  # respeita rate limit da API ML

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

    # Salva estado para o detector de aprovação
    os.makedirs('data', exist_ok=True)
    sugestao = {
        'data': datetime.now().isoformat(),
        'semana': data_semana,
        'mlbs': [item['id'] for item in top7],
        'processado': False,
    }
    with open('data/ultima_sugestao.json', 'w', encoding='utf-8') as f:
        json.dump(sugestao, f, ensure_ascii=False, indent=2)
    print('[OK] data/ultima_sugestao.json salvo')


if __name__ == '__main__':
    main()
