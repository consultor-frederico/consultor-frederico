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
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime, timedelta

# --- 🚨 CONFIGURAÇÕES 🚨 ---
MINHA_CHAVE = "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"
ID_AGENDA = "a497481e5251098078e6c68882a849680f499f6cef836ab976ffccdaad87689a@group.calendar.google.com"
MEU_EMAIL_GOOGLE = "frederico.novotny@gmail.com"  # <--- COLOQUE SEU GMAIL AQUI

st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets', 
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]

NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 
ID_PASTA_RAIZ = '1ZTZ-6-Q46LOQqLTZsxhdefUgsNypNNMS'

# --- FUNÇÕES AUXILIARES ---

def ler_conteudo_arquivo(uploaded_file):
    if uploaded_file is None: return ""
    texto_extraido = ""
    try:
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            for pagina in leitor.pages:
                texto_extraido += pagina.extract_text() + "\n"
        elif uploaded_file.type == "text/plain":
            texto_extraido = str(uploaded_file.read(), "utf-8")
        return f"\n--- CONTEÚDO DO ANEXO ({uploaded_file.name}) ---\n{texto_extraido}\n"
    except Exception as e: return f"\n[Erro leitura: {e}]\n"

def conectar_google():
    try:
        if "google_credentials" in st.secrets:
            info_chaves = json.loads(st.secrets["google_credentials"]["json_data"])
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        return gspread.authorize(creds), build('drive', 'v3', credentials=creds), build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Erro de Conexão: {e}")
        return None, None, None

def consultar_ia(mensagem, sistema, temperatura=0.5):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MINHA_CHAVE}", "Content-Type": "application/json"}
        dados = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": mensagem}], "temperature": temperatura}
        resp = requests.post(url, headers=headers, json=dados).json()
        return resp['choices'][0]['message']['content']
    except: return "Sistema indisponível."

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=2)
    while len(sugestoes) < 10:
        if dia_foco.weekday() >= 5:
            dia_foco += timedelta(days=1)
            continue
        comeco = dia_foco.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
        fim = dia_foco.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=comeco, timeMax=fim, singleEvents=True).execute()
        horas_ocupadas = [int(e['start'].get('dateTime').split('T')[1].split(':')[0]) for e in events_result.get('items', []) if e['start'].get('dateTime')]
        
        dia_txt = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex'][dia_foco.weekday()]})"
        for h in [9, 10, 11, 13, 14, 15, 16, 17]:
            if h not in horas_ocupadas: sugestoes.append(f"{dia_txt} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:10]

def criar_pasta_cliente(service_drive, nome_cliente, nome_servico, arquivo_uploaded):
    try:
        # 1. Criar a Pasta
        meta = {
            'name': f"{datetime.now().strftime('%Y-%m-%d')} - {nome_cliente} - {nome_servico}", 
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [ID_PASTA_RAIZ]
        }
        folder = service_drive.files().create(body=meta, fields='id, webViewLink').execute()
        folder_id = folder.get('id')

        # 2. Upload do Arquivo (se houver)
        if arquivo_uploaded is not None:
            media = MediaIoBaseUpload(arquivo_uploaded, mimetype=arquivo_uploaded.type, resumable=True)
            file_meta = {'name': arquivo_uploaded.name, 'parents': [folder_id]}
            
            # Criar o arquivo
            novo_arquivo = service_drive.files().create(
                body=file_meta, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True
            ).execute()
            
            # TRANSFERIR PROPRIEDADE PARA VOCÊ (Isso resolve a cota 403)
            service_drive.permissions().create(
                fileId=novo_arquivo.get('id'),
                transferOwnership=True,
                body={'type': 'user', 'role': 'owner', 'emailAddress': MEU_EMAIL_GOOGLE}
            ).execute()
        
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        match = re.search(r"(\d{2}/\d{2}).*às (\d{1,2}):(\d{2})", horario_texto)
        if not match: return "Erro Data"
        dia_mes, hora, minuto = match.group(1), int(match.group(2)), int(match.group(3))
        dt_inicio = datetime.strptime(f"{datetime.now().year}/{dia_mes} {hora}:{minuto}", "%Y/%d/%m %H:%M")
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'Tel: {tel}\nSolicitação Web.',
            'start': {'dateTime': dt_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (dt_inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Confirmado"
    except Exception as e: return f"Erro Agenda: {e}"

def salvar_na_planilha(client_sheets, dados, link):
    try:
        sheet = client_sheets.open(NOME_PLANILHA_GOOGLE).sheet1
        sheet.append_row([
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], dados['email'],
            dados['melhor_horario'], dados['servico'], dados['analise_cliente'], dados['analise_tecnica'],
            link, dados['status_agenda']
        ])
    except: pass

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'encerrado' in st.session_state:
        st.success("✅ Sessão Finalizada!")
        if st.button("🔄 Iniciar Nova"):
            st.session_state.clear(); st.rerun()
        return

    st.title("🧮 Consultor Frederico")

    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    
    client_sheets, service_drive, service_calendar = conectar_google()

    # FASE 1: COLETA COM MEMÓRIA
    if st.session_state.fase == 1:
        d = st.session_state.dados_form
        tipo = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True, index=["Advogado", "Empresa", "Colaborador"].index(d.get("tipo", "Advogado")))
        
        nome = st.text_input("Nome", value=d.get("nome", ""))
        tel = st.text_input("WhatsApp", value=d.get("tel", ""))
        
        # Filtro de serviços por perfil
        opcoes = ["Liquidação de Sentença", "Inicial/Estimativa", "Impugnação", "Rescisão", "Horas Extras", "Outros"] if tipo == "Advogado" else ["Rescisão", "Horas Extras", "Outros"]
        servico = st.selectbox("Serviço:", opcoes)
        
        salario = st.text_input("Salário", value=d.get("salario", ""))
        relato = st.text_area("Relato:", value=d.get("relato", ""))

        if st.button("Analisar"):
            st.session_state.dados_form.update({"nome": nome, "tel": tel, "tipo": tipo, "servico": servico, "salario": salario, "relato": relato})
            st.session_state.fase = 2; st.rerun()

    # FASE 2: CONFIRMAÇÃO
    if st.session_state.fase == 2:
        st.info(f"Caso de {st.session_state.dados_form['nome']}")
        col1, col2 = st.columns(2)
        if col2.button("❌ Refazer"): st.session_state.fase = 1; st.rerun()
        if col1.button("✅ Confirmar"): st.session_state.fase = 3; st.rerun()

    # FASE 3: DOCUMENTOS (TRAVA PARA ADVOGADO)
    if st.session_state.fase == 3:
        if st.session_state.dados_form["tipo"] == "Advogado":
            st.session_state.arquivo_anexado = st.file_uploader("Anexar Documento", type=["pdf", "png", "jpg"])
        else:
            st.info("ℹ️ Anexos exclusivos para Advogados.")
            st.session_state.arquivo_anexado = None
            
        if st.button("Ir para Agendamento"): st.session_state.fase = 4; st.rerun()

    # FASE 4: FINALIZAÇÃO (IA MERCADO)
    if st.session_state.fase == 4:
        horarios = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Horário:", horarios)
        
        if st.button("Finalizar"):
            with st.spinner("IA analisando complexidade..."):
                d = st.session_state.dados_form
                prompt = f"Analise dificuldade e valor sugerido para: {d['relato']}."
                analise = consultar_ia(prompt, "Perito Sênior")
                
                link = criar_pasta_cliente(service_drive, d['nome'], d['servico'], st.session_state.get('arquivo_anexado'))
                
                salvar_na_planilha(client_sheets, {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"),
                    "tipo_usuario": d['tipo'], "nome": d['nome'], "telefone": d['tel'], "email": "",
                    "melhor_horario": horario, "servico": d['servico'],
                    "analise_cliente": "OK", "analise_tecnica": analise,
                    "status_agenda": "Confirmado"
                }, link)
                
                st.success(f"Agendado! Pasta: {link}")
                st.markdown(f"**Análise:** {analise}")
                if st.button("Novo"): st.session_state.clear(); st.rerun()

if __name__ == "__main__":
    main()
