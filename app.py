import streamlit as st
import datetime
import json
import random
import requests
import gspread
import re
import PyPDF2
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta

# --- 🚨 CONFIGURAÇÕES 🚨 ---
MINHA_CHAVE = "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"
ID_AGENDA = "a497481e5251098078e6c68882a849680f499f6cef836ab976ffccdaad87689a@group.calendar.google.com"

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets', 
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]

NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 

# --- CALLBACKS DE FORMATAÇÃO ---

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
    except: return "[Erro na leitura do arquivo]"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Erro de Conexão Google: {e}")
        return None, None

def consultar_ia(mensagem, sistema, temperatura=0.3):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {
            "model": "llama-3.1-8b-instant", 
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], 
            "temperature": temperatura
        }
        resp = requests.post(url, headers=headers, json=dados)
        return resp.json()['choices'][0]['message']['content']
    except: return "IA temporariamente indisponível."

def salvar_na_planilha(client_sheets, dados):
    try:
        sh = client_sheets.open(NOME_PLANILHA_GOOGLE)
        sheet = sh.sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Horário", "Serviço", "Resposta Inicial IA", "Complemento Cliente", "Nome do Arquivo", "Análise Total Frederico", "Status Agenda"])
        
        linha = [
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], 
            dados['melhor_horario'], dados['servico'], dados['ia_inicial'], dados['complemento_texto'], 
            dados['nome_arquivo'], dados['analise_pericial'], dados['status_agenda']
        ]
        sheet.append_row(linha)
        return True
    except Exception as e:
        st.error(f"❌ Erro Planilha: {e}")
        return False

# --- FUNÇÕES AGENDA ---
def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=1)
    while len(sugestoes) < 12:
        if dia_foco.weekday() >= 5 or dia_foco.strftime("%d/%m") in FERIADOS_NACIONAIS:
            dia_foco += timedelta(days=1); continue
        inicio_iso = dia_foco.replace(hour=9, minute=0, second=0).isoformat() + 'Z'
        fim_iso = dia_foco.replace(hour=18, minute=0, second=0).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=inicio_iso, timeMax=fim_iso, singleEvents=True).execute()
        events = events_result.get('items', [])
        horas_ocupadas = [datetime.fromisoformat(e['start'].get('dateTime').replace('Z', '')).hour for e in events if 'dateTime' in e['start']]
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in range(9, 18):
            if h != 12 and h not in horas_ocupadas: sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:15]

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        partes = horario_texto.split(" às ")
        data_pt, hora_pt = partes[0].split(" ")[0], partes[1]
        data_c = datetime.strptime(f"{data_pt}/{datetime.now().year} {hora_pt}", "%d/%m/%Y %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'WhatsApp: {tel}',
            'start': {'dateTime': data_c.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (data_c + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'},
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Agendado"
    except: return "Erro Agenda"

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'ia_inicial' not in st.session_state: st.session_state.ia_inicial = ""
    if 'ia_consolidada' not in st.session_state: st.session_state.ia_consolidada = ""
    if 'complemento_texto' not in st.session_state: st.session_state.complemento_texto = "Não enviado"
    if 'nome_arquivo' not in st.session_state: st.session_state.nome_arquivo = "Nenhum"
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""

    client_sheets, service_calendar = conectar_google()

    st.markdown("### Frederico Novotny - Consultor Trabalhista")
    st.divider()

    if st.session_state.fase == 1:
        st.subheader("1. Identificação")
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True)
        nome = st.text_input("Nome/Razão Social")
        tel = st.text_input("WhatsApp", key="tel_input", on_change=formatar_tel_callback)
        servico = st.selectbox("Tipo de Cálculo:", ["Liquidação", "Iniciais", "Rescisão", "Horas Extras", "Outros"])
        
        if st.button("💬 Iniciar Análise"):
            if not nome or not st.session_state.tel_input: st.warning("Preencha os campos.")
            else:
                st.session_state.dados_form.update({"nome": nome, "tel": st.session_state.tel_input, "tipo": tipo, "servico": servico})
                with st.spinner("IA processando..."):
                    p = f"Saúde cordialmente {nome} ({tipo}) que busca {servico}. Seja breve."
                    st.session_state.ia_inicial = consultar_ia(p, "Assistente Jurídico.")
                    st.session_state.fase = 2; st.rerun()

    if st.session_state.fase == 2:
        st.subheader("2. Complemento de Informações")
        st.info(st.session_state.ia_inicial)
        
        # 🆕 AVISO DE PRIVACIDADE E LGPD
        st.warning("🔒 **Compromisso com sua Privacidade (LGPD):** Os documentos enviados não serão salvos em nossos bancos de dados. Eles serão utilizados exclusivamente para esta análise inicial e descartados em seguida.")

        metodo = st.radio("Deseja detalhar seu caso?", ["Sim, digitar relato agora", "Vou anexar documentos para a IA ler"], horizontal=True)

        if metodo == "Sim, digitar relato agora":
            relato_user = st.text_area("Descreva os detalhes aqui:")
            if st.button("Salvar Detalhes"):
                st.session_state.complemento_texto = relato_user
                st.success("Informações recebidas!")
        else:
            st.info("ℹ️ Selecione a opção abaixo e anexe os arquivos na próxima tela. A IA fará a interpretação automática para você.")

        if st.button("✅ Prosseguir"):
            st.session_state.fase = 3; st.rerun()

    if st.session_state.fase == 3:
        st.subheader("3. Análise de Documentos")
        arquivo = st.file_uploader("Anexar PDF ou TXT", type=["pdf", "txt"])
        
        if arquivo: 
            st.session_state.nome_arquivo = arquivo.name
            st.session_state.conteudo_arquivo = ler_conteudo_arquivo(arquivo)
            
            # 🆕 IA INTERPRETA O ARQUIVO E PEDE BREVE RELATO
            with st.spinner("IA interpretando arquivo..."):
                p_interp = f"Interprete brevemente este documento: {st.session_state.conteudo_arquivo[:2000]}. Diga ao usuário o que você identificou e peça que ele escreva abaixo um breve relato do que ele precisa especificamente sobre este arquivo."
                interpretacao = consultar_ia(p_interp, "Assistente Jurídico.")
                st.chat_message("assistant").write(interpretacao)
                
                relato_sobre_doc = st.text_area("Seu relato sobre este documento:", key="relato_doc")
                if st.button("Consolidar Informações"):
                    st.session_state.complemento_texto = relato_sobre_doc
                    with st.spinner("Consolidando..."):
                        p_cons = f"O usuário enviou o arquivo {arquivo.name} e disse: {relato_user if 'relato_user' in locals() else relato_doc}. Traga um entendimento superficial e cordial para o usuário, mostrando que entendeu o pedido."
                        st.session_state.ia_consolidada = consultar_ia(p_cons, "Assistente Jurídico.")
                        st.info(st.session_state.ia_consolidada)
        
        if st.button("🗓️ Ir para Agendamento"): st.session_state.fase = 4; st.rerun()

    if st.session_state.fase == 4:
        st.subheader("4. Agendamento Final")
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha o Horário:", horarios)
        
        if st.button("✅ Finalizar"):
            with st.spinner("Gerando Dossiê Frederico..."):
                d = st.session_state.dados_form
                # 🆕 ANÁLISE COMPLETA PARA O FREDERICO
                p_pericial = f"""
                Você é o Perito do Frederico.
                DADOS: Cliente {d['nome']}, Serviço {d['servico']}.
                RELATO: {st.session_state.complemento_texto}
                DOCUMENTO: {st.session_state.conteudo_arquivo}
                
                TAREFA: Gere uma análise profunda e técnica. Identifique verbas, riscos, inconsistências entre relato e doc, e complexidade do cálculo.
                """
                analise_total_fred = consultar_ia(p_pericial, "Perito Trabalhista Sênior.")
                
                status_agenda = criar_evento_agenda(service_calendar, horario, d['nome'], d['tel'], d['servico'])
                
                salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"), 
                    "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": d['tel'],
                    "melhor_horario": horario, "servico": d['servico'], 
                    "ia_inicial": st.session_state.ia_inicial,
                    "complemento_texto": st.session_state.complemento_texto,
                    "nome_arquivo": st.session_state.nome_arquivo, 
                    "analise_pericial": analise_total_fred, 
                    "status_agenda": status_agenda
                })
                st.session_state.fase = 5; st.rerun()

    if st.session_state.fase == 5:
        st.balloons(); st.success("✅ Tudo pronto! Frederico recebeu sua análise e aguarda você no horário marcado."); st.button("🔄 Novo", on_click=lambda: st.session_state.clear())

if __name__ == "__main__":
    main()
