#!/usr/bin/env python3
"""
gerar_artigo.py — Blog Vida Útil
Ciclo semanal: segunda Leandro aprova → este script roda uma vez →
cria artigo + produto WooCommerce para cada MLB aprovado →
agenda publicação 1 por dia, seg a dom, 09h BRT.
Repete na segunda seguinte com nova sugestão.

Lê  : data/aprovacao_atual.json
Grava: data/aprovacao_atual.json (progresso parcial + processado=True ao fim)
       data/ultima_sugestao.json (processado=True ao fim)
"""

import json
import os
import re
import sys
import time
import unicodedata
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import markdown
import requests

# === Credenciais ===
GEMINI_KEYS = [
    os.environ.get('GEMINI_API_KEY_PRIMARY'),
    os.environ.get('GEMINI_API_KEY_BACKUP'),
]
WP_URL           = os.environ['WORDPRESS_URL'].rstrip('/')
WP_USER          = os.environ['WORDPRESS_USER']
WP_PASS          = os.environ['WORDPRESS_APP_PASSWORD']
TELEGRAM_TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
ML_PUBLISHER_ID  = os.environ.get('ML_PUBLISHER_ID', '65450483')
ML_TRACKING_WORD = os.environ.get('ML_TRACKING_WORD', 'casalemaro')

DATA_DIR       = 'data'
APROVACAO_FILE = f'{DATA_DIR}/aprovacao_atual.json'
SUGESTAO_FILE  = f'{DATA_DIR}/ultima_sugestao.json'

# UTC-3 (BRT, sem horário de verão no Brasil desde 2019)
BRT = timezone(timedelta(hours=-3))

WP_AUTH = b64encode(f'{WP_USER}:{WP_PASS}'.encode()).decode()
WP_HEADERS = {
    'Authorization': f'Basic {WP_AUTH}',
    'Content-Type':  'application/json',
}

# Mapeamento título → categoria WP (blog e WooCommerce usam os mesmos IDs)
CATEGORIA_KEYWORDS = {
    'seguranca': ['câmera', 'camera', 'sensor', 'fechadura', 'alarme', 'vigilância', 'vigilancia'],
    'iluminacao': ['lâmpada', 'lampada', 'led', 'dimmer', 'iluminação', 'iluminacao'],
    'audio':      ['echo', 'alexa', 'google home', 'alto-falante', 'speaker', 'caixa de som'],
    'eletro':     ['robô', 'robo', 'aspirador', 'airfryer', 'air fryer', 'smartband',
                   'smart band', 'geladeira', 'fire tv', 'roku', 'comedouro', 'tv stick'],
}
# IDs de categorias no WP (valem para posts E para WooCommerce)
WP_CATEGORIA_IDS = {
    'seguranca': 20,   # Segurança
    'iluminacao': 19,  # Iluminação Inteligente
    'audio':      21,  # Áudio e Assistentes
    'eletro':     22,  # Eletrodomésticos Inteligentes
    'default':    17,  # Automação Residencial
}

GEMINI_MODEL = 'gemini-2.5-flash'
GEMINI_BASE  = 'https://generativelanguage.googleapis.com/v1beta/models'

BLOCO_CARD = (
    '<!-- wp:hostinger-affiliate-plugin/mercado-block '
    '{{"display_type":"single_product_card","asin":"{mlb}","asin_manual":"{mlb}"}} -->\n'
    '<div class="wp-block-hostinger-affiliate-plugin-mercado-block" '
    'data-asin="{mlb}" data-display-type="single_product_card"></div>\n'
    '<!-- /wp:hostinger-affiliate-plugin/mercado-block -->'
)

BLOCO_DISCLOSURE = (
    '<!-- wp:paragraph {"className":"affiliate-disclosure"} -->\n'
    '<p class="affiliate-disclosure"><em>⚠️ <strong>Aviso de afiliado:</strong> '
    'Este artigo contém links de afiliados. Se você comprar via nossos links, '
    'recebemos uma pequena comissão sem custo adicional para você. '
    'Isso nos ajuda a manter o site gratuito e com conteúdo de qualidade.</em></p>\n'
    '<!-- /wp:paragraph -->'
)

PROMPT_ARTIGO = """Você é redator especialista em Casa Inteligente para o blog Vida Útil (vidautil.com.br).
Escreva um artigo de review completo em português brasileiro sobre: "{nome}" (MLB: {mlb_id}).

Dados reais do produto:
- Preço atual: R$ {preco}
- Unidades vendidas: {vendas}
- Garantia: {garantia}
Especificações técnicas:
{specs}

Descrição do fabricante:
{descricao}

REGRAS INVIOLÁVEIS — CONFORMIDADE ADSENSE:
1. Tom consultivo e imparcial — NÃO escreva como anúncio ou material de marketing
2. Mínimo 1200 palavras de conteúdo genuinamente útil
3. Seção "Desvantagens" obrigatória com MÍNIMO 3 pontos REAIS e honestos
4. NÃO inclua bloco de disclosure (será inserido automaticamente)
5. NÃO inclua blocos do plugin de afiliado (serão inseridos automaticamente)
6. NÃO inclua imagens
7. Use apenas Markdown: ## para h2, ### para h3, **negrito**, tabelas com |

ESTRUTURA OBRIGATÓRIA (nesta ordem exata, sem alterar os placeholders):

## Introdução
(2 parágrafos: problema que o produto resolve + por que o leitor precisa saber)

[PLACEHOLDER_CTA_INICIO]

## Ficha Técnica
(tabela markdown com 10-12 especificações técnicas — use os dados acima + complementos)

## Como Funciona e Recursos na Prática
(3 parágrafos descrevendo funcionamento real)

## Prós e Contras

### Vantagens
(lista com mínimo 4 vantagens baseadas em uso real)

### Desvantagens
(lista com mínimo 3 desvantagens honestas — obrigatório)

## Análise Detalhada

### Instalação e Configuração
(2 parágrafos)

### Conectividade e Aplicativo
(2 parágrafos)

### Compatibilidade com Assistentes de Voz
(2 parágrafos)

### Custo-benefício
(2 parágrafos com análise honesta de preço)

## Comparativo com Concorrentes
(2 produtos similares reais disponíveis no Brasil, com análise honesta de qual é melhor e por quê)

## Para Quem é Indicado
(2 parágrafos: perfis que SE BENEFICIAM e perfis que NÃO devem comprar)

[PLACEHOLDER_CTA_MEIO]

## Perguntas Frequentes (FAQ)
(6 perguntas e respostas práticas sobre uso, instalação e compatibilidade)

## Conclusão
(2 parágrafos: síntese honesta, NÃO apenas elogios)

[PLACEHOLDER_CTA_FINAL]
"""


# === Funções auxiliares ===

def ler_json(path: str, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def salvar_json(path: str, data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def slugify(texto: str) -> str:
    texto = unicodedata.normalize('NFD', texto)
    texto = ''.join(c for c in texto if unicodedata.category(c) != 'Mn')
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-9\s-]', '', texto)
    texto = re.sub(r'\s+', '-', texto.strip())
    texto = re.sub(r'-+', '-', texto)
    return texto[:90]


def detectar_categoria(titulo: str) -> int:
    titulo_lower = titulo.lower()
    for cat, keywords in CATEGORIA_KEYWORDS.items():
        if any(kw in titulo_lower for kw in keywords):
            return WP_CATEGORIA_IDS[cat]
    return WP_CATEGORIA_IDS['default']


def formatar_preco(v) -> str:
    try:
        return f'{float(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(v)


def link_afiliado_ml(mlb_id: str) -> str:
    return (
        f'https://www.mercadolivre.com.br/affiliates/items'
        f'?id={mlb_id}'
        f'&publisher_id={ML_PUBLISHER_ID}'
        f'&tracking_word={ML_TRACKING_WORD}'
    )


# === ML API ===

def buscar_produto_ml(mlb_id: str) -> dict | None:
    headers = {'User-Agent': 'Blog-Vida-Util-Bot/1.0'}

    r = requests.get(
        f'https://api.mercadolibre.com/items/{mlb_id}',
        headers=headers, timeout=15,
    )
    if not r.ok:
        print(f'[ERRO] ML API {r.status_code} para {mlb_id}')
        return None
    item = r.json()

    specs = []
    for attr in item.get('attributes', []):
        nome  = attr.get('name', '')
        valor = attr.get('value_name', '')
        if nome and valor and valor != 'N/A':
            specs.append(f'- {nome}: {valor}')

    # Imagem principal do anúncio
    pictures   = item.get('pictures', [])
    imagem_url = pictures[0].get('url', '') if pictures else item.get('thumbnail', '')

    # Descrição do produto
    descricao = ''
    rd = requests.get(
        f'https://api.mercadolibre.com/items/{mlb_id}/descriptions',
        headers=headers, timeout=15,
    )
    if rd.ok:
        descs = rd.json()
        if descs:
            descricao = descs[0].get('plain_text', '')[:800]

    return {
        'id':         mlb_id,
        'title':      item.get('title', mlb_id),
        'price':      item.get('price', 0),
        'vendas':     item.get('sold_quantity', 0),
        'garantia':   item.get('warranty', 'Verificar anúncio'),
        'specs':      '\n'.join(specs[:15]) or 'Verificar anúncio no Mercado Livre',
        'descricao':  descricao or 'Produto para casa inteligente disponível no Mercado Livre.',
        'imagem_url': imagem_url,
        'permalink':  item.get('permalink', ''),
    }


# === Gemini API ===

def chamar_gemini(prompt: str) -> str | None:
    for chave in GEMINI_KEYS:
        if not chave:
            continue
        url  = f'{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={chave}'
        body = {
            'contents':        [{'parts': [{'text': prompt}]}],
            'generationConfig': {'temperature': 0.7, 'maxOutputTokens': 8192},
        }
        try:
            r = requests.post(url, json=body, timeout=120)
            if r.status_code == 429:
                print(f'[WARN] Gemini quota esgotada — tentando próxima chave')
                time.sleep(5)
                continue
            r.raise_for_status()
            candidates = r.json().get('candidates', [])
            if candidates:
                return candidates[0]['content']['parts'][0]['text']
        except Exception as e:
            print(f'[ERRO] Gemini: {e}')
            time.sleep(3)
    return None


def gerar_artigo_gemini(produto: dict) -> str | None:
    prompt = PROMPT_ARTIGO.format(
        nome=produto['title'],
        mlb_id=produto['id'],
        preco=formatar_preco(produto['price']),
        vendas=produto['vendas'],
        garantia=produto['garantia'],
        specs=produto['specs'],
        descricao=produto['descricao'],
    )
    texto = chamar_gemini(prompt)
    time.sleep(6)  # respeita rate limit Gemini entre chamadas
    return texto


# === Processamento de conteúdo ===

def montar_conteudo_wp(artigo_md: str, mlb_id: str) -> str:
    card = BLOCO_CARD.format(mlb=mlb_id)

    conteudo = artigo_md
    conteudo = conteudo.replace(
        '[PLACEHOLDER_CTA_INICIO]',
        f'\n\n{BLOCO_DISCLOSURE}\n\n{card}\n\n',
    )
    conteudo = conteudo.replace('[PLACEHOLDER_CTA_MEIO]',  f'\n\n{card}\n\n')
    conteudo = conteudo.replace('[PLACEHOLDER_CTA_FINAL]', f'\n\n{card}\n\n')

    return markdown.markdown(conteudo, extensions=['tables', 'extra'])


# === WordPress — Post ===

def calcular_proxima_data() -> datetime:
    """
    Retorna a próxima data livre para agendamento (1/dia, 09h BRT = 12h UTC).
    Se ainda não passou das 08h45 BRT (detection de 08h30 chegou a tempo),
    agenda para HOJE — senão para amanhã.
    Consulta posts futuros no WP para não colidir com artigos já agendados.
    """
    agora_brt = datetime.now(BRT)
    hoje_brt  = agora_brt.date()

    # Janela de 15 min após a detection de 08h30: se < 08h45 → agenda hoje
    corte = agora_brt.replace(hour=8, minute=45, second=0, microsecond=0)
    inicio = hoje_brt if agora_brt < corte else hoje_brt + timedelta(days=1)

    r = requests.get(
        f'{WP_URL}/wp-json/wp/v2/posts',
        headers=WP_HEADERS,
        params={'status': 'future', 'per_page': 20, 'orderby': 'date', 'order': 'desc'},
        timeout=15,
    )
    posts_futuros = r.json() if r.ok and isinstance(r.json(), list) else []

    if posts_futuros:
        ultima_str = posts_futuros[0].get('date', '')
        try:
            ultima  = datetime.fromisoformat(ultima_str).date()
            proxima = max(ultima + timedelta(days=1), inicio)
        except ValueError:
            proxima = inicio
    else:
        proxima = inicio

    # 09h BRT = 12h UTC
    return datetime(proxima.year, proxima.month, proxima.day, 12, 0, 0, tzinfo=timezone.utc)


def publicar_wp(produto: dict, conteudo: str, data_pub: datetime) -> int | None:
    titulo    = produto['title']
    slug      = slugify(titulo) + '-vale-a-pena'
    categoria = detectar_categoria(titulo)
    date_str  = data_pub.strftime('%Y-%m-%dT%H:%M:%S')

    payload = {
        'title':          titulo,
        'slug':           slug,
        'content':        conteudo,
        'status':         'future',
        'date_gmt':       date_str,
        'categories':     [categoria],
        'comment_status': 'open',
        'meta':           {'_kad_post_feature_position': 'right center'},
    }
    r = requests.post(
        f'{WP_URL}/wp-json/wp/v2/posts',
        headers=WP_HEADERS, json=payload, timeout=30,
    )
    if not r.ok:
        print(f'[ERRO] WP post {r.status_code}: {r.text[:300]}')
        return None

    post_id = r.json().get('id')
    link    = r.json().get('link', '')
    data_brt = data_pub.astimezone(BRT).strftime('%d/%m %H:%Mh BRT')
    print(f'[OK] Post ID {post_id} agendado {data_brt} — {link}')
    return post_id


# === WooCommerce — Produto na loja ===

def criar_produto_wc(produto: dict) -> int | None:
    """
    Cria produto do tipo 'external' na loja WooCommerce.
    Ao clicar em 'Ver no Mercado Livre', o cliente é redirecionado
    para o link de afiliado — sem armazenar pagamentos.
    """
    titulo    = produto['title']
    categoria = detectar_categoria(titulo)
    preco_str = str(produto['price'])
    link_ml   = link_afiliado_ml(produto['id'])

    # Descrição curta: primeiras 2 frases da descrição ML
    desc_curta = produto['descricao']
    frases = re.split(r'(?<=[.!?])\s+', desc_curta)
    short_desc = ' '.join(frases[:2]) if frases else desc_curta

    payload: dict = {
        'name':             titulo,
        'type':             'external',
        'external_url':     link_ml,
        'button_text':      'Ver no Mercado Livre',
        'regular_price':    preco_str,
        'short_description': short_desc,
        'description':      produto['descricao'],
        'categories':       [{'id': categoria}],
        'status':           'publish',
    }

    # Imagem do produto (WooCommerce tenta baixar via src)
    if produto.get('imagem_url'):
        payload['images'] = [{'src': produto['imagem_url'], 'alt': titulo}]

    r = requests.post(
        f'{WP_URL}/wp-json/wc/v3/products',
        headers=WP_HEADERS, json=payload, timeout=30,
    )
    if not r.ok:
        print(f'[ERRO] WC produto {r.status_code}: {r.text[:300]}')
        return None

    wc_id = r.json().get('id')
    print(f'[OK] Produto WooCommerce ID {wc_id} criado — {titulo[:50]}')
    return wc_id


# === Telegram ===

def enviar_telegram(texto: str):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    requests.post(url, json={
        'chat_id':                  TELEGRAM_CHAT_ID,
        'text':                     texto,
        'parse_mode':               'HTML',
        'disable_web_page_preview': True,
    }, timeout=15)


# === Main ===

def main():
    print(f'[INFO] {datetime.now().isoformat()} — geração de artigos iniciada')

    aprovacao = ler_json(APROVACAO_FILE)
    if not aprovacao:
        print('[ERRO] data/aprovacao_atual.json não encontrado')
        sys.exit(1)

    if aprovacao.get('processado'):
        print(f'[INFO] Semana {aprovacao.get("semana")} já processada — nada a fazer')
        sys.exit(0)

    mlbs_aprovados   = aprovacao.get('mlbs_aprovados', [])
    mlbs_processados = set(aprovacao.get('mlbs_processados', []))
    mlbs_pendentes   = [m for m in mlbs_aprovados if m not in mlbs_processados]

    if not mlbs_pendentes:
        aprovacao['processado'] = True
        salvar_json(APROVACAO_FILE, aprovacao)
        sys.exit(0)

    print(f'[INFO] {len(mlbs_pendentes)} produtos para processar: {mlbs_pendentes}')

    proxima_data  = calcular_proxima_data()
    data_brt_ini  = proxima_data.astimezone(BRT).strftime('%d/%m')
    data_brt_fim  = (proxima_data + timedelta(days=len(mlbs_pendentes)-1)).astimezone(BRT).strftime('%d/%m')
    print(f'[INFO] Agendamento: {data_brt_ini} a {data_brt_fim} (09h BRT cada)')

    # Inicializa da sessão anterior (tolerante a reexecuções parciais)
    posts_criados = aprovacao.get('posts_criados', [])

    for mlb_id in mlbs_pendentes:
        print(f'\n[INFO] ── {mlb_id} ──')

        produto = buscar_produto_ml(mlb_id)
        if not produto:
            print(f'[WARN] ML API falhou para {mlb_id} — pulando')
            continue

        print(f'[INFO] {produto["title"]} | R$ {formatar_preco(produto["price"])}')

        # Gera artigo com Gemini
        artigo_md = gerar_artigo_gemini(produto)
        if not artigo_md:
            print(f'[ERRO] Gemini falhou para {mlb_id} — pulando')
            continue

        palavras = len(re.sub(r'\[.*?\]|\<[^>]+>', '', artigo_md).split())
        print(f'[INFO] Artigo: {palavras} palavras')

        conteudo_wp = montar_conteudo_wp(artigo_md, mlb_id)

        # Publica artigo no WordPress
        post_id = publicar_wp(produto, conteudo_wp, proxima_data)
        if not post_id:
            print(f'[ERRO] WP falhou para {mlb_id} — pulando')
            continue

        # Cria produto na loja WooCommerce
        wc_id = criar_produto_wc(produto)

        posts_criados.append({
            'mlb_id':      mlb_id,
            'post_id':     post_id,
            'wc_id':       wc_id,
            'titulo':      produto['title'],
            'imagem_url':  produto['imagem_url'],
            'slug':        slugify(produto['title']) + '-vale-a-pena',
            'capa_gerada': False,
            'data_pub':    proxima_data.isoformat(),
        })

        # Salva progresso parcial — gerar_capa.py e atualizar_planilha.py leem posts_criados daqui
        mlbs_processados.add(mlb_id)
        aprovacao['mlbs_processados'] = list(mlbs_processados)
        aprovacao['posts_criados']    = posts_criados
        salvar_json(APROVACAO_FILE, aprovacao)

        proxima_data += timedelta(days=1)  # próximo artigo: +1 dia (seg→dom→seg...)

    # Marca semana como concluída
    if mlbs_processados >= set(mlbs_aprovados):
        aprovacao['processado'] = True
        salvar_json(APROVACAO_FILE, aprovacao)
        sugestao = ler_json(SUGESTAO_FILE, {})
        sugestao['processado'] = True
        salvar_json(SUGESTAO_FILE, sugestao)
        print('\n[OK] Todos os produtos da semana processados')

    # Resumo Telegram
    if posts_criados:
        linhas = [
            f'<b>✅ Semana {aprovacao.get("semana", "")} — pipeline concluído</b>',
            f'{len(posts_criados)} artigos + produtos criados:\n',
        ]
        for p in posts_criados:
            data_pub = datetime.fromisoformat(p['data_pub']).astimezone(BRT)
            wc_info  = f'WC #{p["wc_id"]}' if p.get('wc_id') else 'WC: falhou'
            linhas.append(
                f'• <b>{p["titulo"][:45]}</b>\n'
                f'  📅 {data_pub.strftime("%d/%m %H:%Mh BRT")} | Post #{p["post_id"]} | {wc_info}\n'
                f'  <code>{p["mlb_id"]}</code>'
            )
        linhas.append('\n<i>⚠️ Capas serão adicionadas manualmente — pipeline 9c pendente</i>')
        enviar_telegram('\n'.join(linhas))

    print(f'\n[OK] {len(posts_criados)}/{len(mlbs_pendentes)} produtos processados com sucesso')


if __name__ == '__main__':
    main()
