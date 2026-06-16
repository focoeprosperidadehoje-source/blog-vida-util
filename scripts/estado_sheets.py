#!/usr/bin/env python3
"""
estado_sheets.py — Blog Vida Útil
Estado transitório do pipeline (sugestão semanal, aprovação pendente, offset do
Telegram) guardado na aba "estado_pipeline" da planilha controle_publicacoes —
em vez dos antigos arquivos data/*.json comitados no repositório público.

`aprovacao_atual` guarda título, MLB, slug e URL de imagem dos produtos da
semana ANTES da publicação; manter isso só na planilha (privada, acesso restrito
à service account + quem Leandro compartilhar) evita expor a fila de produtos
no histórico do Git público.

Esquema da aba "estado_pipeline" (criada automaticamente se não existir):
| chave              | valor_json                         | atualizado_em |
|--------------------|-------------------------------------|---------------|
| ultima_sugestao    | JSON da sugestão semanal            | ISO datetime  |
| aprovacao_atual    | JSON da aprovação + posts_criados   | ISO datetime  |
| telegram_offset    | JSON {"ultimo_update_id": N}        | ISO datetime  |

Reaproveita o mesmo padrão gspread + GOOGLE_DRIVE_CREDENTIALS de
`atualizar_planilha.py`.
"""

import json
import os
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = '1cH1KUvgSt2OFTBTfzcaDOFDA4OPUEfNiKCvDtJklMwU'
ABA_ESTADO     = 'estado_pipeline'

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]

CABECALHO = ['chave', 'valor_json', 'atualizado_em']


def _conectar() -> gspread.Worksheet:
    creds_str = os.environ.get('GOOGLE_DRIVE_CREDENTIALS', '')
    if not creds_str:
        raise ValueError('GOOGLE_DRIVE_CREDENTIALS não configurada')

    creds_info = json.loads(creds_str)
    creds      = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    gc         = gspread.authorize(creds)
    sh         = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet(ABA_ESTADO)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ABA_ESTADO, rows=10, cols=len(CABECALHO))
        ws.update('A1', [CABECALHO])
    return ws


def _linha_da_chave(ws: gspread.Worksheet, chave: str) -> int | None:
    """Retorna o índice 1-based da linha que guarda `chave`, ou None se ausente."""
    valores = ws.get_all_values()
    for i, linha in enumerate(valores[1:], start=2):  # linha 1 = cabeçalho
        if linha and linha[0] == chave:
            return i
    return None


def ler_estado(chave: str, default=None):
    """Lê e desserializa o JSON guardado na linha de `chave`. Retorna `default` se ausente."""
    try:
        ws = _conectar()
    except Exception as e:
        print(f'[ERRO] estado_sheets.ler_estado("{chave}"): {e}')
        return default

    linha = _linha_da_chave(ws, chave)
    if linha is None:
        return default

    valor_json = ws.cell(linha, 2).value or ''
    try:
        return json.loads(valor_json) if valor_json else default
    except json.JSONDecodeError:
        print(f'[WARN] estado_sheets: JSON inválido na chave "{chave}" — usando default')
        return default


def salvar_estado(chave: str, data) -> None:
    """Serializa `data` em JSON e grava/atualiza a linha de `chave` na aba estado_pipeline."""
    ws         = _conectar()
    valor_json = json.dumps(data, ensure_ascii=False)
    agora      = datetime.now().isoformat()

    linha = _linha_da_chave(ws, chave)
    if linha is None:
        ws.append_row([chave, valor_json, agora], value_input_option='RAW')
    else:
        ws.update(f'A{linha}:C{linha}', [[chave, valor_json, agora]], value_input_option='RAW')
