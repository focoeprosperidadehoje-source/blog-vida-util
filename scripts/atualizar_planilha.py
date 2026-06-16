#!/usr/bin/env python3
"""
atualizar_planilha.py — Blog Vida Útil
Atualiza a planilha controle_publicacoes no Google Sheets
com os dados dos artigos publicados na semana.
Para cada post em data/aprovacao_atual.json:
  - Se mlb_id já existe na planilha → atualiza colunas
  - Se não existe → insere nova linha
Lê: data/aprovacao_atual.json
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

DATA_DIR        = 'data'
APROVACAO_FILE  = f'{DATA_DIR}/aprovacao_atual.json'
SPREADSHEET_ID  = '1cH1KUvgSt2OFTBTfzcaDOFDA4OPUEfNiKCvDtJklMwU'

BRT = timezone(timedelta(hours=-3))

# Colunas da planilha (1-based, conforme estrutura criada em 13/06/2026)
COL = {
    'mlb_id':            1,
    'slug':              2,
    'post_id':           3,
    'semana':            4,
    'artigo_publicado':  5,
    'data_artigo':       6,
    'capa_gerada':       7,
    'video_publicado':   8,
    'data_video':        9,
    'carousel_publicado': 10,
    'data_carousel':     11,
    'imagens_apagadas':  12,
    'data_apagado':      13,
}

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]


def ler_json(path: str, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def conectar_sheets() -> gspread.Worksheet:
    creds_str = os.environ.get('GOOGLE_DRIVE_CREDENTIALS', '')
    if not creds_str:
        raise ValueError('GOOGLE_DRIVE_CREDENTIALS não configurada')

    creds_info = json.loads(creds_str)
    creds      = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    gc         = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).sheet1


def atualizar_linha(ws: gspread.Worksheet, row_idx: int, post: dict, data_hoje: str):
    """Atualiza as colunas de um artigo já existente na planilha."""
    updates = [
        (row_idx, COL['slug'],             post.get('slug', '')),
        (row_idx, COL['post_id'],          str(post.get('post_id', ''))),
        (row_idx, COL['semana'],           post.get('semana', data_hoje)),
        (row_idx, COL['artigo_publicado'], 'TRUE'),
        (row_idx, COL['data_artigo'],      data_hoje),
        (row_idx, COL['capa_gerada'],      'TRUE' if post.get('capa_gerada') else 'FALSE'),
    ]
    for row, col, val in updates:
        ws.update_cell(row, col, val)
    print(f'[OK] Linha {row_idx} atualizada (post #{post.get("post_id", "")})')


def inserir_linha(ws: gspread.Worksheet, post: dict, data_hoje: str):
    """Insere nova linha para um artigo novo (MLB não encontrado na planilha)."""
    nova_linha = [''] * len(COL)
    nova_linha[COL['mlb_id'] - 1]            = post['mlb_id']
    nova_linha[COL['slug'] - 1]              = post.get('slug', '')
    nova_linha[COL['post_id'] - 1]           = str(post.get('post_id', ''))
    nova_linha[COL['semana'] - 1]            = post.get('semana', data_hoje)
    nova_linha[COL['artigo_publicado'] - 1]  = 'TRUE'
    nova_linha[COL['data_artigo'] - 1]       = data_hoje
    nova_linha[COL['capa_gerada'] - 1]       = 'TRUE' if post.get('capa_gerada') else 'FALSE'
    nova_linha[COL['video_publicado'] - 1]   = 'FALSE'
    nova_linha[COL['carousel_publicado'] - 1] = 'FALSE'
    nova_linha[COL['imagens_apagadas'] - 1]  = 'FALSE'
    ws.append_row(nova_linha, value_input_option='USER_ENTERED')
    print(f'[OK] Nova linha inserida (post #{post.get("post_id", "")})')


def main():
    aprovacao = ler_json(APROVACAO_FILE)
    if not aprovacao:
        print('[ERRO] data/aprovacao_atual.json não encontrado')
        sys.exit(1)

    posts = aprovacao.get('posts_criados', [])
    if not posts:
        print('[INFO] Nenhum post em posts_criados — nada a atualizar')
        sys.exit(0)

    print(f'[INFO] Conectando ao Google Sheets...')
    try:
        ws = conectar_sheets()
    except Exception as e:
        print(f'[ERRO] Google Sheets: {e}')
        sys.exit(1)

    # Lê todos os dados uma vez para evitar múltiplas chamadas
    todas_linhas = ws.get_all_values()  # lista de listas (inclui cabeçalho na linha 1)
    # Mapeia mlb_id → índice de linha (1-based, pula cabeçalho)
    mlb_para_linha = {}
    for i, linha in enumerate(todas_linhas[1:], start=2):  # linha 1 = cabeçalho
        mlb = linha[COL['mlb_id'] - 1] if linha else ''
        if mlb:
            mlb_para_linha[mlb] = i

    data_hoje = datetime.now(BRT).strftime('%Y-%m-%d')
    semana    = aprovacao.get('semana', data_hoje)

    ok = 0
    for post in posts:
        mlb_id = post.get('mlb_id', '')
        if not mlb_id:
            continue

        # Adiciona semana ao post para usar na planilha
        post['semana'] = semana

        try:
            if mlb_id in mlb_para_linha:
                atualizar_linha(ws, mlb_para_linha[mlb_id], post, data_hoje)
            else:
                inserir_linha(ws, post, data_hoje)
            ok += 1
        except Exception as e:
            print(f'[ERRO] Sheets para post #{post.get("post_id", "")}: {e}')

    print(f'\n[OK] {ok}/{len(posts)} linhas atualizadas na planilha')


if __name__ == '__main__':
    main()
