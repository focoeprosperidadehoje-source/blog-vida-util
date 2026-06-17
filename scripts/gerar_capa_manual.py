#!/usr/bin/env python3
"""
gerar_capa_manual.py — Blog Vida Útil
Fix pontual para posts que ficaram fora do estado "posts_criados" (Sheets) e por
isso nunca tiveram capa gerada por gerar_capa.py. Recebe os dados do post via
variáveis de ambiente e roda o mesmo pipeline (remove.bg + Pexels + Pillow).
"""

import os
import sys

from gerar_capa import processar_post, enviar_telegram


def main():
    post = {
        'post_id':    int(os.environ['CAPA_POST_ID']),
        'titulo':     os.environ['CAPA_TITULO'],
        'imagem_url': os.environ['CAPA_IMAGEM_URL'],
        'slug':       os.environ.get('CAPA_SLUG', ''),
        'mlb_id':     os.environ.get('CAPA_MLB_ID', ''),
    }
    sucesso, erro = processar_post(post)
    if sucesso:
        enviar_telegram(f'✅ Capa manual aplicada — Post #{post["post_id"]}')
        print('[OK] capa aplicada')
    else:
        enviar_telegram(f'❌ Falha na capa manual — Post #{post["post_id"]}\n⛔ {erro}')
        print(f'[ERRO] {erro}')
    sys.exit(0 if sucesso else 1)


if __name__ == '__main__':
    main()
