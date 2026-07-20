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

MLB_RE_CONTENT = re.compile(r'data-asin="(MLB\d+)"')

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

# MLBs já publicados no blog — baseline histórico. Em runtime, main() expande
# este set dinamicamente consultando o WordPress (buscar_mlbs_publicados_no_wp).
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
    # MLB46314703: causou duplicidade (posts 822 e 832 com o mesmo produto,
    # ver CLAUDE.md 17/06/2026) — post 822 nunca tinha sido registrado aqui
    # por ter entrado no fluxo fora do estado rastreado pelo pipeline.
    'MLB46314703',
    # MLB61815112: produto que substituiu o duplicado no post 822 (Tp-Link
    # Tapo C206 360° — ver trocar_produto.yml, 17/06/2026).
    'MLB61815112',
}

# Palavras-chave que excluem produto por ser fora do nicho Casa Inteligente.
# Adicionado em 20/07/2026 após "Par GBIC SFP 10G 10km LC BIDI" ser sugerido
# e publicado 6x — produto de datacenter passou pelo filtro de busca ML.
KEYWORDS_EXCLUIR_NICHO = {
    'gbic', 'sfp', 'transceptor', 'fibra óptica', 'fibra optica',
    'rack', 'switch gerenciável', 'switch gerenciavel',
    'cabo ethernet', 'cabo de rede', 'notebook', 'laptop',
    'monitor', 'teclado', 'mouse', 'celular', 'smartphone',
    'tablet', 'impressora', 'projetor', 'servidor', 'nobreak',
    'modem', 'drone',
}

# Cotas de sugestão por categoria (total = 7)
COTAS_CATEGORIA = {
    'eletro':     2,  # Eletrodomésticos Inteligentes
    'automacao':  2,  # Automação Residencial (tomadas, interruptores, hubs)
    'iluminacao': 1,  # Iluminação Inteligente
    'seguranca':  1,  # Segurança (câmeras, fechaduras, sensores)
    'livre':      1,  # Slot livre — maior score entre todos os restantes
}
TOTAL_SUGESTAO = sum(COTAS_CATEGORIA.values())  # 7

# IDs de categorias no blog WP — usados para cruzar déficit
WP_CATEGORIA_IDS_BLOG = {
    'seguranca':  25,
    'iluminacao': 26,
    'eletro':     28,
    'automacao':  24,
}

CATEGORIA_EMOJI = {
    'automacao':  '🏠',
    'seguranca':  '📷',
    'iluminacao': '💡',
    'eletro':     '⚡',
}
CATEGORIA_LABEL = {
    'automacao':  'Automação',
    'seguranca':  'Segurança',
    'iluminacao': 'Iluminação',
    'eletro':     'Eletro',
}


def parse_preco_brl(texto: str) -> float:
    """Converte 'R$ 1.234,56' (já sem o prefixo) para float 1234.56."""
    try:
        return float(texto.replace('.', '').replace(',', '.'))
    except ValueError:
        return 0.0


def is_nicho_casa_inteligente(titulo: str) -> bool:
    """Rejeita produto que contenha palavra-chave claramente off-nicho."""
    t = titulo.lower()
    return not any(kw in t for kw in KEYWORDS_EXCLUIR_NICHO)


def detectar_categoria_sugestao(titulo: str) -> str:
    """Classifica produto em uma das categorias de sugestão semanal."""
    t = titulo.lower()
    if any(kw in t for kw in ['câmera', 'camera', 'sensor', 'fechadura', 'alarme',
                               'vigilância', 'vigilancia']):
        return 'seguranca'
    if any(kw in t for kw in ['lâmpada', 'lampada', 'led', 'dimmer',
                               'iluminação', 'iluminacao', 'fita led', 'strip led']):
        return 'iluminacao'
    if any(kw in t for kw in ['robô', 'robo', 'aspirador', 'airfryer', 'air fryer',
                               'smart band', 'smartband', 'geladeira', 'echo', 'alexa',
                               'fire tv', 'roku', 'tv stick', 'comedouro']):
        return 'eletro'
    return 'automacao'


def buscar_mlbs_publicados_no_wp() -> set:
    """
    Retorna MLB IDs já presentes em posts do WordPress (qualquer status).
    Extrai via data-asin no content.rendered — não depende de set hardcoded.
    """
    mlbs = set()
    for pagina in range(1, 10):  # máx 900 posts
        try:
            r = requests.get(
                f'{WP_URL}/wp-json/wp/v2/posts',
                headers=WP_HEADERS,
                params={'per_page': 100, 'page': pagina, 'status': 'any',
                        '_fields': 'content'},
                timeout=20,
            )
        except Exception:
            break
        if not r.ok:
            break
        posts = r.json() if isinstance(r.json(), list) else []
        if not posts:
            break
        for post in posts:
            content = post.get('content', {}).get('rendered', '') or ''
            mlbs.update(MLB_RE_CONTENT.findall(content))
        if len(posts) < 100:
            break
    return mlbs


def buscar_contagem_wp_por_categoria() -> dict:
    """Conta posts (any status) por categoria no WordPress para calcular déficit."""
    contagem = {k: 0 for k in WP_CATEGORIA_IDS_BLOG}
    for cat_key, cat_id in WP_CATEGORIA_IDS_BLOG.items():
        try:
            r = requests.get(
                f'{WP_URL}/wp-json/wp/v2/posts',
                headers=WP_HEADERS,
                params={'categories': cat_id, 'per_page': 1, 'status': 'any'},
                timeout=15,
            )
            if r.ok:
                contagem[cat_key] = int(r.headers.get('X-WP-Total', 0))
        except Exception:
            pass
    return contagem


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
    """Filtra produto: não publicado, dentro da faixa de preço e no nicho Casa Inteligente."""
    iid = item.get('id', '')
    preco = item.get('price') or 0
    titulo = item.get('title', '')
    return (
        iid not in MLB_PUBLICADOS
        and PRECO_MIN <= preco <= PRECO_MAX
        and is_nicho_casa_inteligente(titulo)
    )


def formatar_preco(v: float) -> str:
    """Formata R$ 1.234,56"""
    return f'R$ {v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def montar_mensagem(top7: list, data_semana: str) -> str:
    # Distribuição por categoria para o cabeçalho
    dist: dict = {}
    for item in top7:
        cat = item.get('_categoria', 'automacao')
        dist[cat] = dist.get(cat, 0) + 1
    dist_linha = ' | '.join(
        f'{CATEGORIA_EMOJI.get(k, "🏠")} {CATEGORIA_LABEL.get(k, k)} ×{v}'
        for k, v in dist.items()
    )

    linhas = [
        '<b>🏠 Sugestão Semanal — Casa Inteligente</b>',
        f'<b>📅 Semana de {data_semana}</b>',
        f'<i>📊 {dist_linha}</i>',
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
        cat = item.get('_categoria', 'automacao')
        cat_emoji = CATEGORIA_EMOJI.get(cat, '🏠')

        linhas.append(
            f'<b>{i}. {cat_emoji} {nome}</b>\n'
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

    # Expande MLB_PUBLICADOS com posts reais do WordPress — garante dedup sem
    # depender de atualização manual do set hardcoded acima.
    try:
        mlbs_wp = buscar_mlbs_publicados_no_wp()
        MLB_PUBLICADOS.update(mlbs_wp)
        print(f'[INFO] {len(mlbs_wp)} MLBs encontrados no WP '
              f'(total exclusão: {len(MLB_PUBLICADOS)})')
    except Exception as e:
        print(f'[WARN] Falha ao buscar MLBs do WP: {e} — usando apenas lista estática')

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

    # Score base ordenado (usado como fallback para slots "livre")
    candidatos.sort(key=lambda x: x['_score'], reverse=True)

    # === Seleção por cota de categoria ===
    # Busca contagem atual para ponderar score por déficit de categoria
    try:
        contagem_wp = buscar_contagem_wp_por_categoria()
        max_artigos = max(contagem_wp.values(), default=0)
        # Categorias com menos artigos publicados ganham bônus de 5% por artigo faltante
        deficit_peso = {k: 1.0 + max(0, max_artigos - v) * 0.05
                        for k, v in contagem_wp.items()}
        print(f'[INFO] Posts WP por categoria: {contagem_wp}')
    except Exception as e:
        print(f'[WARN] Falha ao buscar contagem WP: {e}')
        deficit_peso = {}

    # Classifica candidatos por categoria e aplica peso de déficit
    buckets: dict = {k: [] for k in COTAS_CATEGORIA}
    for item in candidatos:
        cat = detectar_categoria_sugestao(item.get('title', ''))
        item['_categoria'] = cat
        peso = deficit_peso.get(cat, 1.0)
        item['_score_final'] = item['_score'] * peso
        bucket_key = cat if cat in buckets else 'automacao'
        buckets[bucket_key].append(item)

    for k in buckets:
        buckets[k].sort(key=lambda x: x.get('_score_final', 0), reverse=True)

    # Preenche slots por cota
    selecionados: list = []
    ids_selecionados: set = set()

    for cat, cota in COTAS_CATEGORIA.items():
        if cat == 'livre':
            continue
        adicionados = 0
        for item in buckets.get(cat, []):
            if adicionados >= cota:
                break
            if item['id'] not in ids_selecionados:
                selecionados.append(item)
                ids_selecionados.add(item['id'])
                adicionados += 1

    # Slot livre: melhor score_final restante entre todos os candidatos
    restantes = sorted(
        [c for c in candidatos if c['id'] not in ids_selecionados],
        key=lambda x: x.get('_score_final', 0), reverse=True,
    )
    for item in restantes[:COTAS_CATEGORIA['livre']]:
        selecionados.append(item)
        ids_selecionados.add(item['id'])

    # Completa slots se algum bucket ficou sem candidatos suficientes
    if len(selecionados) < TOTAL_SUGESTAO:
        for item in restantes[COTAS_CATEGORIA['livre']:]:
            if len(selecionados) >= TOTAL_SUGESTAO:
                break
            if item['id'] not in ids_selecionados:
                selecionados.append(item)
                ids_selecionados.add(item['id'])

    top7 = selecionados[:TOTAL_SUGESTAO]

    data_semana = datetime.now().strftime('%d/%m/%Y')
    mensagem = montar_mensagem(top7, data_semana)
    enviar_telegram(mensagem)

    print(f'[OK] Top {len(top7)} enviados via Telegram')

    # Salva estado para o detector de aprovação — planilha Google Sheets, não
    # arquivo no repositório (evita comitar título/MLB no histórico Git público)
    # IMPORTANTE: salva os dados completos do produto (título, preço, imagem,
    # link, rating) capturados AGORA via search-items — não só o MLB ID. Isso
    # evita que gerar_artigo.py precise chamar api.mercadolibre.com de novo
    # depois da aprovação (chamada que retorna 403 a partir do GitHub Actions —
    # ver memória pipeline_sugestao_semanal_bloqueios, item 2b).
    itens_salvos = [
        {
            'id':           item['id'],
            'title':        item['title'],
            'price':        item.get('price') or 0,
            'image_url':    item.get('image_url', ''),
            'permalink':    item.get('permalink', ''),
            'rating':       item.get('rating', 0),
            'review_count': item.get('review_count', 0),
        }
        for item in top7
    ]
    sugestao = {
        'data': datetime.now().isoformat(),
        'semana': data_semana,
        'mlbs': [item['id'] for item in top7],
        'itens': itens_salvos,
        'processado': False,
    }
    salvar_estado('ultima_sugestao', sugestao)
    print('[OK] estado "ultima_sugestao" salvo na planilha (aba estado_pipeline)')


if __name__ == '__main__':
    main()
