import streamlit as st
import json
import logging
import re
import time
from io import BytesIO
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

import PyPDF2
import gspread
import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- 🔐 CONFIGURAÇÕES VIA SECRETS ---
MINHA_CHAVE = st.secrets["MINHA_CHAVE"]
ID_AGENDA = st.secrets["ID_AGENDA"]

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

logger = logging.getLogger("consultor_frederico")
TZ = ZoneInfo("America/Sao_Paulo")

# --- IA ---
MODELO_GEMINI = "gemini-2.5-flash"
MODELO_GROQ = "llama-3.1-8b-instant"
MAX_TENTATIVAS_IA = 4          # tentativas com espera crescente (1s, 2s, 4s, 8s)
TIMEOUT_IA = 30                # segundos por requisição
CODIGOS_RETENTAVEL = (429, 500, 502, 503, 504)
MENSAGEM_IA_INDISPONIVEL = (
    "No momento não consegui gerar a análise automática. "
    "Seu pedido foi registrado; siga para o agendamento."
)

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]

NOME_PLANILHA_GOOGLE = 'Atendimento_Fred'

# Logo da marca (assets/logo.png, na raiz do repositório)
import os
LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")

# --- CALLBACKS DE FORMATAÇÃO ---

def formatar_cnpj_callback():
    val = st.session_state.cnpj_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 14:
        st.session_state.cnpj_input = f"{limpo[:2]}.{limpo[2:5]}.{limpo[5:8]}/{limpo[8:12]}-{limpo[12:]}"

def formatar_data_adm_callback():
    val = st.session_state.adm_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.adm_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

def formatar_data_sai_callback():
    val = st.session_state.sai_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.sai_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

def formatar_data_prazo_callback():
    val = st.session_state.prazo_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 8:
        st.session_state.prazo_input = f"{limpo[:2]}/{limpo[2:4]}/{limpo[4:]}"

def formatar_salario_callback():
    val = st.session_state.sal_input
    if not val: return
    temp = val.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        valor_float = float(temp)
        st.session_state.sal_input = f"R$ {valor_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except ValueError:
        pass

def formatar_tel_callback():
    val = st.session_state.tel_input
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"

# --- FUNÇÕES DE SISTEMA ---

def ler_conteudo_arquivo(uploaded_file):
    if uploaded_file is None: return ""
    try:
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            texto = "\n".join([p.extract_text() for p in leitor.pages if p.extract_text()])
            return texto
        return str(uploaded_file.read(), "utf-8")
    except Exception:
        logger.exception("Falha ao ler o arquivo anexado")
        return "[Erro na leitura técnica do arquivo]"

def conectar_google():
    try:
        info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
        creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        logger.exception("Falha na conexão com o Google")
        st.error(f"❌ Erro de Conexão Google: {e}")
        return None, None

def consultar_ia(mensagem, sistema):
    """
    Envia a mensagem ao provedor de IA (Groq se a chave começa com 'gsk_', senão Google Gemini).
    Retenta com espera crescente apenas em erros temporários (limite de uso, instabilidade).
    Em falha definitiva, registra o motivo no log e devolve uma mensagem neutra para o cliente.
    """
    chave = MINHA_CHAVE.strip()

    if chave.startswith("gsk_"):
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}
        dados = {
            "model": MODELO_GROQ,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": mensagem}
            ],
            "temperature": 0.3
        }
        extrair = lambda j: j['choices'][0]['message']['content']
    else:
        # A chave vai no cabeçalho, não na URL, para não aparecer em logs.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO_GEMINI}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": chave}
        dados = {
            "contents": [{"parts": [{"text": mensagem}]}],
            "systemInstruction": {"parts": [{"text": sistema}]}
        }
        extrair = lambda j: j['candidates'][0]['content']['parts'][0]['text']

    ultimo_erro = "sem resposta"
    for tentativa in range(MAX_TENTATIVAS_IA):
        try:
            resp = requests.post(url, headers=headers, json=dados, timeout=TIMEOUT_IA)
            if resp.status_code == 200:
                return extrair(resp.json())
            ultimo_erro = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code not in CODIGOS_RETENTAVEL:
                break  # erro definitivo (chave inválida, modelo errado etc.): não adianta repetir
        except (requests.RequestException, KeyError, IndexError, ValueError) as err:
            ultimo_erro = repr(err)
        time.sleep(2 ** tentativa)

    logger.error("IA indisponível após %s tentativa(s). Último erro: %s", tentativa + 1, ultimo_erro)
    return MENSAGEM_IA_INDISPONIVEL

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now(TZ) + timedelta(days=1)
    while len(sugestoes) < 12:
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
            dia_foco += timedelta(days=1); continue
        # Janela em horário de Brasília, com fuso explícito (antes saía deslocada em 3h).
        inicio = dia_foco.replace(hour=9, minute=0, second=0, microsecond=0)
        fim = dia_foco.replace(hour=18, minute=0, second=0, microsecond=0)
        try:
            events_result = service_calendar.events().list(
                calendarId=ID_AGENDA,
                timeMin=inicio.isoformat(),
                timeMax=fim.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            horas_ocupadas = [
                datetime.fromisoformat(e['start']['dateTime']).astimezone(TZ).hour
                for e in events_result.get('items', [])
                if 'dateTime' in e.get('start', {})
            ]
        except Exception:
            logger.exception("Falha ao consultar a agenda; horários podem estar desatualizados")
            horas_ocupadas = []
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in range(9, 18):
            if h != 12 and h not in horas_ocupadas: sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:15]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        partes = horario_texto.split(" às ")
        data_pt, hora_pt = partes[0].split(" ")[0], partes[1]
        ano = datetime.now(TZ).year
        data_c = datetime.strptime(f"{data_pt}/{ano} {hora_pt}", "%d/%m/%Y %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'WhatsApp: {tel}',
            'start': {'dateTime': data_c.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (data_c + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado com Sucesso"
    except Exception:
        logger.exception("Falha ao criar evento na agenda para %s", horario_texto)
        return "Erro Agenda"

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE); sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome/Razão", "Responsável", "Contato", "Email", "CNPJ", "Horário", "Serviço", "Data Prazo", "Relato Inicial", "IA Inicial", "Relato Comp.", "IA Resposta Comp.", "Arquivo", "Análise Profunda Fred", "Status"])
        linha = [
            dados['data_hora'], dados['tipo'], dados['nome'], dados.get('resp', ''), dados['tel'], dados['email'], dados.get('cnpj', ''),
            dados['melhor_horario'], dados['servico'], dados.get('prazo', ''), dados['relato'], dados['ia_inicial'],
            dados['complemento_relato'], dados['ia_resposta_complementar'], dados['nome_arquivo'], dados['analise_profunda'], dados['status_agenda']
        ]
        sheet.append_row(linha)
        return True
    except Exception:
        logger.exception("Falha ao salvar o atendimento na planilha")
        return False

def gerar_entendimento(dados, complemento, conteudo_doc):
    """Resumo mostrado ao cliente: prova que entendemos o pedido, sem valores nem análise final."""
    trecho_doc = conteudo_doc[:6000] if conteudo_doc else "Nenhum documento anexado."
    p_res = (f"RELATO INICIAL: {dados.get('relato', '')} | COMPLEMENTO: {complemento} | "
             f"DOCUMENTO (trecho): {trecho_doc}. "
             "Tarefa: mostre ao cliente que entendeu o pedido. Liste em tópicos curtos o que foi entendido "
             "(serviço, período, verbas ou pontos citados). NÃO calcule nem estime valores, não dê opinião "
             "jurídica nem prognóstico. Termine dizendo que o Consultor Frederico fará a análise completa. Ao citar o profissional, use somente 'Consultor Frederico' (nunca 'Dr.' ou 'Dra.'). Não use tratamento de cortesia para o cliente no resumo.")
    return consultar_ia(p_res, "Assistente Jurídico.")

# --- APLICAÇÃO PRINCIPAL ---
def main():
    # CSS PARA ESCONDER ÍCONES
    st.markdown("""<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;} .stAppDeployButton {display:none;} [data-testid="stStatusWidget"] {visibility: hidden;} .viewerBadge_container__1QSob {display:none !important;}</style>""", unsafe_allow_html=True)

    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""
    if 'ia_resposta_complementar' not in st.session_state: st.session_state.ia_resposta_complementar = "Sem complemento"
    if 'relato_complementar' not in st.session_state: st.session_state.relato_complementar = "Não enviado"
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""
    if 'nome_arquivo' not in st.session_state: st.session_state.nome_arquivo = "Não enviado"
    if 'resumo_pedido' not in st.session_state: st.session_state.resumo_pedido = ""

    client_sheets, service_calendar = conectar_google()

    # Cabeçalho com a marca: logo centralizado e subtítulo na paleta azul-marinho e dourado
    # Faixa azul-marinho que acomoda o logo sem depender do fundo branco da página
    logo_b64 = open(os.path.join(os.path.dirname(LOGO_PATH), "logo_b64.txt")).read().strip()
    st.markdown(
        f"""<div style='background:#1B2A4A; border-radius:14px; padding:18px; text-align:center; margin-bottom:6px;'>
            <img src='data:image/png;base64,{logo_b64}' style='width:420px; max-width:100%; border-radius:10px;' alt='Frederico Novotny - Cálculos Trabalhistas'/>
        </div>""",
        unsafe_allow_html=True,
    )
    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        d = st.session_state.dados_form
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, index=["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado")))

        col1, col2 = st.columns(2)
        if tipo == "Empresa":
            nome = col1.text_input("Razão Social", value=d.get("nome", ""))
            cnpj = col2.text_input("CNPJ", key="cnpj_input", on_change=formatar_cnpj_callback, placeholder="00.000.000/0000-00", value=d.get("cnpj", ""))
            resp = st.text_input("Responsável", value=d.get("resp", ""))
            email = st.text_input("E-mail", value=d.get("email", ""))
            tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
        else:
            nome = col1.text_input("Nome Completo", value=d.get("nome", ""))
            email = col2.text_input("E-mail", value=d.get("email", ""))
            tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback, value=d.get("tel", ""))
            cnpj = ""; resp = nome

        opcoes = ["Liquidação", "Iniciais", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        servico = st.selectbox("Serviço:", opcoes, index=opcoes.index(d.get("servico")) if d.get("servico") in opcoes else 0)

        c_adm, c_sai = st.columns(2)
        adm = c_adm.text_input("Admissão (DDMMAAAA)", key="adm_input", on_change=formatar_data_adm_callback, value=d.get("adm", ""))
        sai = c_sai.text_input("Saída (DDMMAAAA)", key="sai_input", on_change=formatar_data_sai_callback, value=d.get("sai", ""))

        col_sal, col_prazo = st.columns(2)
        salario = col_sal.text_input("Salário Base", key="sal_input", on_change=formatar_salario_callback, value=d.get("salario", ""))
        if tipo == "Advogado":
            prazo = col_prazo.text_input("Data Prazo/Citação", key="prazo_input", on_change=formatar_data_prazo_callback, value=d.get("prazo", ""))
        else: prazo = ""

        relato = st.text_area("Explique sua demanda:", value=d.get("relato", ""))

        if st.button("💬 Analisar Solicitação"):
            if not nome or not tel: st.warning("Por favor, preencha Nome e WhatsApp.")
            else:
                st.session_state.dados_form.update({"nome": nome, "resp": resp, "tel": tel, "email": email, "cnpj": cnpj, "tipo": tipo, "servico": servico, "adm": adm, "sai": sai, "salario": salario, "relato": relato, "prazo": prazo})
                with st.spinner("Analisando..."):
                    p = f"Aja como assistente do Consultor Frederico. CLIENTE: {nome} | PERFIL: {tipo} | SERVIÇO: {servico}. REGRAS: trate o cliente por Dr/Dra SOMENTE se o PERFIL for Advogado; para Empresa ou Colaborador use Sr/Sra. Vá direto à saudação. Diga que o pedido foi recebido e que o Consultor Frederico fará a análise. Ao citar o profissional, use somente 'Consultor Frederico' (nunca 'Dr.' ou 'Dra.'). NÃO mencione valores, salários, honorários, prazos ou resultados. Convide a complementar com detalhes e documentos e a agendar."
                    st.session_state.ia_inicial = consultar_ia(p, "Assistente Jurídico.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_inicial)
        opcao = st.radio("Deseja complementar?", ["Apenas seguir para agenda", "Adicionar detalhes", "Enviar documentos"], horizontal=True)
        if opcao == "Adicionar detalhes":
            rel_comp = st.text_area("Complemento:", value=st.session_state.relato_complementar if st.session_state.relato_complementar != "Não enviado" else "")
            if st.button("Salvar Detalhes"):
                st.session_state.relato_complementar = rel_comp
                p_comp = f"O usuário {st.session_state.dados_form['nome']} ({st.session_state.dados_form['tipo']}) complementou: {rel_comp}. Responda cordial. Trate o cliente por Dr/Dra SOMENTE se o perfil for Advogado; para Empresa ou Colaborador use Sr/Sra. Ao citar o profissional, use somente Consultor Frederico."
                st.session_state.ia_resposta_complementar = consultar_ia(p_comp, "Assistente Jurídico.")
                st.session_state.resumo_pedido = ""
                st.rerun()
        elif opcao == "Enviar documentos":
            arquivo = st.file_uploader("Anexar PDF", type=["pdf"])
            if arquivo:
                st.session_state.nome_arquivo = arquivo.name
                st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
                st.session_state.resumo_pedido = ""
                st.success("Arquivo pronto.")

        if not st.session_state.resumo_pedido:
            with st.spinner("Lendo seu pedido..."):
                st.session_state.resumo_pedido = gerar_entendimento(
                    st.session_state.dados_form, st.session_state.relato_complementar, st.session_state.conteudo_arquivo)
        st.markdown("**Entendemos que o seu pedido é:**")
        st.info(st.session_state.resumo_pedido)
        if st.session_state.conteudo_arquivo:
            st.caption("Os documentos anexados foram usados para montar este entendimento.")
        if st.button("🔄 Atualizar entendimento"):
            with st.spinner("Atualizando..."):
                st.session_state.resumo_pedido = gerar_entendimento(
                    st.session_state.dados_form, st.session_state.relato_complementar, st.session_state.conteudo_arquivo)
            st.rerun()

        c1, c2 = st.columns(2)
        if c1.button("✅ Confirmar e Agendar"): st.session_state.fase = 4; st.rerun()
        if c2.button("❌ Corrigir Dados"): st.session_state.fase = 1; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("🗓️ Escolha seu Horário")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Selecione um horário disponível:", horarios)
        if st.button("✅ Finalizar Agendamento"):
            with st.spinner("Concluindo..."):
                d = st.session_state.dados_form
                p_fred = f"PERITO: Analise INTEGRALMENTE: Relato 1: {d['relato']} | Relato 2: {st.session_state.relato_complementar} | Doc: {st.session_state.conteudo_arquivo}. Serviço: {d['servico']} | Salário: {d['salario']} | Prazo: {d['prazo']}. Forneça dificuldade, verbas, honorários sugeridos e riscos."
                analise = consultar_ia(p_fred, "Perito Sênior")
                status = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                salvo = salvar_na_planilha(client_sheets, {**d, "data_hora": datetime.now(TZ).strftime("%d/%m %H:%M"), "melhor_horario": horario, "relato_inicial": d['relato'], "ia_inicial": st.session_state.ia_inicial, "complemento_relato": st.session_state.relato_complementar, "ia_resposta_complementar": st.session_state.ia_resposta_complementar, "nome_arquivo": st.session_state.nome_arquivo, "analise_profunda": analise, "status_agenda": status})
                if not salvo or status != "Agendado com Sucesso":
                    logger.error("Atendimento de %s concluído com falhas (planilha=%s, agenda=%s)", d['nome'], salvo, status)
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Solicitação enviada! Frederico Novotny entrará em contato em breve."); st.button("🔄 Novo Atendimento", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
