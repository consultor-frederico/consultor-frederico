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
# MUITO IMPORTANTE: Troque pelo seu e-mail do Gmail para receber a posse dos arquivos
MEU_EMAIL_GOOGLE = "frederico.novotny@gmail.com" 

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
        # 1. Criar Pasta
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
            
            # Criar arquivo
            file = service_drive.files().create(
                body=file_meta, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True
            ).execute()
            
            # TRANSFERIR PROPRIEDADE (Resolve o erro 403 de cota)
            service_drive.permissions().create(
                fileId=file.get('id'),
                transferOwnership=True,
                body={'type': 'user', 'role': 'owner', 'emailAddress': MEU_EMAIL_GOOGLE}
            ).execute()
        
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

# --- APLICAÇÃO PRINCIPAL ---
def main():
    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    
    st.image("https://cdn-icons-png.flaticon.com/512/2643/2643501.png", width=90)
    st.title("Consultor Frederico - Cálculos Trabalhistas")

    client_sheets, service_drive, service_calendar = conectar_google()

    # --- FASE 1: COLETA COM MEMÓRIA E FILTROS ---
    if st.session_state.fase == 1:
        d = st.session_state.dados_form
        
        # Recupera o perfil ou inicia como Advogado
        perfil_opcoes = ["Advogado", "Empresa", "Colaborador"]
        perfil_idx = perfil_opcoes.index(d.get("tipo", "Advogado"))
        tipo = st.radio("Perfil:", perfil_opcoes, horizontal=True, index=perfil_idx)
        
        col1, col2 = st.columns(2)
        nome = col1.text_input("Nome/Razão Social", value=d.get("nome", ""))
        salario = st.text_input("Salário Base", value=d.get("salario", ""))

        # FILTRO DE OPÇÕES DE CÁLCULO POR PERFIL
        if tipo == "Advogado":
            opcoes_calculo = ["Liquidação de Sentença", "Inicial/Estimativa", "Impugnação", "Rescisão", "Horas Extras", "Outros"]
        else:
            opcoes_calculo = ["Rescisão", "Horas Extras", "Outros"]
        
        # Garante que o index salvo ainda existe na lista filtrada
        try:
            current_serv_idx = opcoes_calculo.index(d.get("servico", ""))
        except:
            current_serv_idx = 0

        servico = st.selectbox("Tipo de Cálculo:", opcoes_calculo, index=current_serv_idx)
        relato = st.text_area("Resumo da Demanda:", value=d.get("relato", ""), height=100)

        if st.button("💬 Analisar Solicitação"):
            st.session_state.dados_form.update({
                "nome": nome, 
                "tipo": tipo, 
                "salario": salario, 
                "relato": relato, 
                "servico": servico
            })
            st.session_state.fase = 2
            st.rerun()

    # --- FASE 2: CONFIRMAÇÃO ---
    elif st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(f"Analisando caso de: **{st.session_state.dados_form['nome']}**")
        st.write(f"Serviço: {st.session_state.dados_form['servico']}")
        
        col_s, col_n = st.columns(2)
        if col_n.button("❌ Não (Refazer)"): 
            st.session_state.fase = 1
            st.rerun()
        if col_s.button("✅ Sim, prosseguir"): 
            st.session_state.fase = 3
            st.rerun()

    # --- FASE 3: DOCUMENTOS (TRAVA DE PERFIL) ---
    elif st.session_state.fase == 3:
        st.subheader("3. Documentos")
        if st.session_state.dados_form["tipo"] == "Advogado":
            st.session_state.arquivo_anexado = st.file_uploader("Anexar Documento", type=["pdf", "png", "jpg"])
        else:
            st.warning("⚠️ A funcionalidade de anexar documentos é exclusiva para perfis de Advogado.")
            st.session_state.arquivo_anexado = None
        
        if st.button("Ir para Agendamento"): 
            st.session_state.fase = 4
            st.rerun()

    # --- FASE 4: FINALIZAÇÃO (IA DE MERCADO) ---
    elif st.session_state.fase == 4:
        st.subheader("🗓️ Finalizar Agendamento")
        opcoes_h = buscar_horarios_livres(service_calendar)
        horario = st.selectbox("Escolha o Horário:", opcoes_h)
        
        if st.button("✅ Confirmar"):
            with st.spinner("IA analisando complexidade e valores..."):
                d = st.session_state.dados_form
                
                # Prompt atualizado para dificuldade e preço
                prompt = (f"Analise o relato: {d['relato']}. "
                          f"Aponte a dificuldade do cálculo (Baixa, Média, Alta) e "
                          f"estipule um valor sugerido para os honorários conforme o mercado de 2026.")
                
                analise = consultar_ia(prompt, "Perito Judicial Sênior")
                
                # Executa criação da pasta e upload com transferência de posse
                link = criar_pasta_cliente(service_drive, d['nome'], d['servico'], st.session_state.get('arquivo_anexado'))
                
                st.success(f"Agendamento concluído! Pasta do cliente: {link}")
                st.markdown(f"### 🚩 Análise Técnica e Comercial:\n{analise}")
                
                if st.button("Novo Atendimento"): 
                    st.session_state.clear()
                    st.rerun()

if __name__ == "__main__":
    main()
