"""
Bot DOM Salvador -> Google Chat
Busca a edição do dia do Diário Oficial do Município de Salvador,
filtra atos relacionados a tecnologia e publica num espaço do Google Chat.

Variáveis de ambiente:
  GCHAT_WEBHOOK_URL   (obrigatória) URL do webhook do espaço
  ANTHROPIC_API_KEY   (opcional) para resumo com IA
"""
import os, re, io, json, datetime as dt
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import pdfplumber

DOM_HOME = "http://www.dom.salvador.ba.gov.br/"
STATE_FILE = "ultima_edicao.txt"
WEBHOOK = os.environ["GCHAT_WEBHOOK_URL"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Ajuste livremente a lista de termos
TERMOS = [
    r"\bSMART\b", r"\bSEMIT\b", r"\bTIC\b", r"tecnologia da informa", r"software",
    r"licen[çc]a de uso", r"licenciamento", r"nuvem", r"cloud", r"data ?center",
    r"sistema de gest", r"link de (internet|dados)", r"fibra [óo]ptica", r"\bredes?\b de dados",
    r"computador", r"notebook", r"servidor(es)? de rede", r"ciberseguran", r"seguran[çc]a da informa",
    r"\bLGPD\b", r"telecomunica", r"outsourcing de impress", r"transforma[çc][ãa]o digital",
    r"intelig[êe]ncia artificial", r"\bERP\b", r"desenvolvimento de sistemas",
]
REGEX = re.compile("|".join(TERMOS), re.IGNORECASE)
HEADERS = {"User-Agent": "Mozilla/5.0 (bot-dom-smart)"}


def achar_pdf_do_dia():
    """Procura na página inicial do DOM o link do PDF da edição mais recente.
    ATENÇÃO: confira a estrutura atual do site e ajuste o seletor se necessário."""
    html = requests.get(DOM_HOME, headers=HEADERS, timeout=60).text
    soup = BeautifulSoup(html, "html.parser")
    links = [urljoin(DOM_HOME, a["href"]) for a in soup.find_all("a", href=True)
             if ".pdf" in a["href"].lower()]
    return links[0] if links else None


def extrair_texto(url_pdf):
    conteudo = requests.get(url_pdf, headers=HEADERS, timeout=180).content
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def trechos_tic(texto, janela=500):
    """Retorna trechos ao redor das ocorrências, sem sobreposição."""
    trechos, fim_anterior = [], -1
    for m in REGEX.finditer(texto):
        ini, fim = max(0, m.start() - janela), min(len(texto), m.end() + janela)
        if ini <= fim_anterior:
            trechos[-1] = (trechos[-1][0], fim)
        else:
            trechos.append((ini, fim))
        fim_anterior = fim
    return [re.sub(r"\s+", " ", texto[i:f]).strip() for i, f in trechos]


def resumir_com_ia(trechos):
    prompt = (
        "Abaixo estão trechos do Diário Oficial do Município de Salvador que mencionam "
        "tecnologia. Faça um apanhado objetivo em português, em tópicos curtos, só com "
        "atos realmente ligados a TIC. Para cada um: tipo de ato (extrato de contrato, "
        "aviso de licitação, homologação, portaria etc.), órgão, objeto, empresa e valor "
        "quando houver. Ignore menções irrelevantes. Se nada for relevante, responda "
        "apenas 'Nenhum ato de TIC identificado.'. Use *negrito* no estilo do Google Chat, "
        "sem markdown de títulos.\n\n" + "\n---\n".join(trechos)[:150000]
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-sonnet-5", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=180,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def enviar_chat(texto):
    # Limite do Google Chat: ~4.000 caracteres por mensagem
    partes = [texto[i:i + 3900] for i in range(0, len(texto), 3900)]
    for p in partes:
        requests.post(WEBHOOK, json={"text": p}, timeout=30).raise_for_status()


def main():
    url_pdf = achar_pdf_do_dia()
    if not url_pdf:
        print("PDF não encontrado.")
        return
    ultima = open(STATE_FILE).read().strip() if os.path.exists(STATE_FILE) else ""
    if url_pdf == ultima:
        print("Edição já enviada.")
        return

    texto = extrair_texto(url_pdf)
    trechos = trechos_tic(texto)
    hoje = dt.date.today().strftime("%d/%m/%Y")

    if not trechos:
        corpo = "Nenhuma menção a tecnologia encontrada nesta edição."
    elif ANTHROPIC_KEY:
        corpo = resumir_com_ia(trechos)
    else:
        corpo = "\n\n".join(f"• {t[:600]}…" for t in trechos[:15])

    enviar_chat(f"📰 *DOM Salvador – {hoje}*\n{url_pdf}\n\n*Apanhado de TIC:*\n{corpo}")
    with open(STATE_FILE, "w") as f:
        f.write(url_pdf)
    print("Enviado.")


if __name__ == "__main__":
    main()
