#!/usr/bin/env python3
"""
detectar_aprovacao.py — Blog Vida Útil
Verifica se Leandro respondeu "aprovado" ao bot no Telegram.
Lê o estado "ultima_sugestao", filtra MLBs excluídos, salva o estado "aprovacao_atual"
— ambos na aba estado_pipeline da planilha Google Sheets (não em arquivo do repositório).
Exit 0 = aprovado e salvo | Exit 1 = ainda não aprovado | Exit 2 = erro/sem sugestão pendente
"""

import os
import re
import sys
from datetime import datetime

import requests

from estado_sheets import ler_estado, salvar_estado

TELEGRAM_TOKEN   = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = os.environ['TELEGRAM_CHAT_ID']


def get_updates(offset: int) -> list:
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates'
    params = {'offset': offset, 'limit': 100, 'timeout': 0}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get('result', [])
    except Exception as e:
        print(f'[ERRO] getUpdates falhou: {e}')
        return []


def parse_exclusoes(texto: str) -> set:
    """Extrai MLBs excluídos de mensagens como 'aprovado sem MLB123, MLB456'."""
    return set(re.findall(r'MLB\d+', texto, re.IGNORECASE))


def main():
    # 1. Verifica se há sugestão pendente
    sugestao = ler_estado('ultima_sugestao')
    if not sugestao:
        print('[INFO] Nenhuma sugestão pendente encontrada (estado "ultima_sugestao" ausente)')
        sys.exit(2)

    if sugestao.get('processado'):
        print(f'[INFO] Sugestão de {sugestao.get("semana")} já foi processada')
        sys.exit(2)

    mlbs_sugeridos = sugestao.get('mlbs', [])
    if not mlbs_sugeridos:
        print('[ERRO] estado "ultima_sugestao" sem MLBs')
        sys.exit(2)

    print(f'[INFO] Sugestão pendente: {len(mlbs_sugeridos)} MLBs da semana {sugestao.get("semana")}')

    # 2. Lê offset do Telegram (evita reprocessar mensagens antigas)
    offset_data  = ler_estado('telegram_offset', {'ultimo_update_id': 0})
    ultimo_id    = offset_data.get('ultimo_update_id', 0)
    proximo_offset = ultimo_id + 1 if ultimo_id else 0

    # 3. Busca atualizações no Telegram
    updates = get_updates(proximo_offset)
    print(f'[INFO] {len(updates)} update(s) novos no Telegram')

    aprovado     = False
    mlbs_excluir = set()
    novo_offset  = ultimo_id

    for upd in updates:
        novo_offset = max(novo_offset, upd.get('update_id', 0))

        msg = upd.get('message') or upd.get('channel_post')
        if not msg:
            continue

        chat_id  = str(msg.get('chat', {}).get('id', ''))
        texto    = msg.get('text', '').strip().lower()

        if chat_id != str(TELEGRAM_CHAT_ID):
            continue

        if 'aprovado' in texto:
            aprovado     = True
            mlbs_excluir = parse_exclusoes(msg.get('text', ''))
            data_msg     = datetime.fromtimestamp(msg.get('date', 0)).isoformat()
            print(f'[OK] "aprovado" detectado em {data_msg}')
            if mlbs_excluir:
                print(f'[INFO] MLBs excluídos por Leandro: {mlbs_excluir}')
            break

    # 4. Sempre atualiza o offset para não reprocessar
    salvar_estado('telegram_offset', {'ultimo_update_id': novo_offset})

    if not aprovado:
        print('[INFO] Aprovação ainda não recebida — aguardando')
        sys.exit(1)

    # 5. Calcula MLBs aprovados (sugeridos - excluídos)
    mlbs_aprovados = [m for m in mlbs_sugeridos if m not in mlbs_excluir]
    if not mlbs_aprovados:
        print('[ERRO] Todos os MLBs foram excluídos — nada a publicar')
        sys.exit(2)

    # 6. Salva aprovação
    aprovacao = {
        'data_aprovacao':  datetime.now().isoformat(),
        'semana':          sugestao.get('semana'),
        'mlbs_aprovados':  mlbs_aprovados,
        'mlbs_processados': [],
        'processado':      False,
    }
    salvar_estado('aprovacao_atual', aprovacao)
    print(f'[OK] {len(mlbs_aprovados)} MLBs aprovados salvos no estado "aprovacao_atual"')
    sys.exit(0)


if __name__ == '__main__':
    main()
