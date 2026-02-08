import streamlit as st
import datetime
import json  # 📂 Essencial para ler os Secrets do Streamlit
import random
import requests
import gspread
import re
import PyPDF2
from io import BytesIO
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload  # 📤 Essencial para enviar arquivos ao Drive
from datetime import datetime, timedelta

# --- 🚨 SUAS CONFIGURAÇÕES 🚨 ---
MINHA_CHAVE = "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"  # <--- COLOQUE SUA CHAVE AQUI
ID_AGENDA = "a497481e5251098078e6c68882a849680f499f6cef836ab976ffccdaad87689a@group.calendar.google.com"      # <--- SEU ID DA AGENDA AQUI

# --- Configurações da Página ---
st.set_page_config(page_title="Consultor Frederico - Cálculos", page_icon="🧮")

# --- Feriados Nacionais ---
FERIADOS_NACIONAIS = ["01/01", "21/04", "01/05", "07/09", "12/10", "02/11", "15/11", "25/12"]

# --- Configurações do Google ---
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets', 
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/calendar'
]
ARQUIVO_CREDENCIAIS = 'credentials.json'
NOME_PLANILHA_GOOGLE = 'Atendimento_Fred' 
NOME_PASTA_DRIVE_PAI = 'Atendimento_Juridico' 

# --- FUNÇÕES AUXILIARES ---

def ler_conteudo_arquivo(uploaded_file):
    """Lê texto de PDF ou TXT"""
    if uploaded_file is None: return ""
    texto_extraido = ""
    try:
        if uploaded_file.type == "application/pdf":
            leitor = PyPDF2.PdfReader(uploaded_file)
            for pagina in leitor.pages:
                texto_extraido += pagina.extract_text() + "\n"
        elif uploaded_file.type == "text/plain":
            texto_extraido = str(uploaded_file.read(), "utf-8")
        return f"\n--- CONTEÚDO DO ANEXO ({uploaded_file.name}) ---\n{texto_extraido}\n-----------------------------------\n"
    except Exception as e: return f"\n[Erro leitura: {e}]\n"

def validar_cnpj(cnpj):
    cnpj = re.sub(r'[^0-9]', '', cnpj)
    if len(cnpj) != 14 or len(set(cnpj)) == 1: return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma1 = sum(int(a) * b for a, b in zip(cnpj[:12], pesos1))
    resto1 = soma1 % 11
    digito1 = 0 if resto1 < 2 else 11 - resto1
    if int(cnpj[12]) != digito1: return False
    pesos2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    soma2 = sum(int(a) * b for a, b in zip(cnpj[:13], pesos2))
    resto2 = soma2 % 11
    digito2 = 0 if resto2 < 2 else 11 - resto2
    if int(cnpj[13]) != digito2: return False
    return True

def callback_formatar_telefone():
    """Formata o telefone no Session State assim que o usuário digita"""
    val = st.session_state.tel_input
    if not val: return
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10:
        st.session_state.tel_input = f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"

def formatar_telefone(val):
    if not val: return ""
    limpo = re.sub(r'\D', '', str(val))
    if len(limpo) == 11:
        return f"({limpo[:2]}) {limpo[2:7]}-{limpo[7:]}"
    elif len(limpo) == 10:
        return f"({limpo[:2]}) {limpo[2:6]}-{limpo[6:]}"
    return val

def formatar_nome_com_titulo(nome, perfil):
    if not nome: return ""
    primeiro_nome = nome.split()[0].title()
    ultimo_caracter = primeiro_nome[-1].lower()
    genero_fem = True if ultimo_caracter == 'a' else False
    titulo = "Dra." if (perfil == 'Advogado' and genero_fem) else "Dr." if perfil == 'Advogado' else "Sra." if genero_fem else "Sr."
    return f"{titulo} {primeiro_nome}"

def conectar_google():
    try:
        # 1. Tenta ler do painel de Secrets do Streamlit (Para o Deploy)
        if "google_credentials" in st.secrets:
            info_json = st.secrets["google_credentials"]["json_data"]
            info_chaves = json.loads(info_json)
            creds = Credentials.from_service_account_info(info_chaves, scopes=SCOPES)
        else:
            # 2. Caso você esteja testando no seu computador (Local)
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

def eh_dia_util(data):
    if data.weekday() >= 5: return False
    return data.strftime("%d/%m") not in FERIADOS_NACIONAIS

def buscar_horarios_livres(service_calendar):
    sugestoes = []
    dia_foco = datetime.now() + timedelta(days=2)
    while not eh_dia_util(dia_foco): dia_foco += timedelta(days=1)
    horarios_permitidos = [9, 10, 11, 13, 14, 15, 16, 17]
    while len(sugestoes) < 6:
        if not eh_dia_util(dia_foco):
            dia_foco += timedelta(days=1)
            continue
        comeco = dia_foco.replace(hour=0, minute=0, second=0).isoformat() + 'Z'
        fim = dia_foco.replace(hour=23, minute=59, second=59).isoformat() + 'Z'
        events_result = service_calendar.events().list(calendarId=ID_AGENDA, timeMin=comeco, timeMax=fim, singleEvents=True).execute()
        horas_ocupadas = [int(e['start'].get('dateTime').split('T')[1].split(':')[0]) for e in events_result.get('items', []) if e['start'].get('dateTime')]
        
        dia_formatado = f"{dia_foco.strftime('%d/%m')} ({['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'][dia_foco.weekday()]})"
        for h in horarios_permitidos:
            if h not in horas_ocupadas: sugestoes.append(f"{dia_formatado} às {h}:00")
        dia_foco += timedelta(days=1)
    return sugestoes[:10]

def criar_pasta_cliente(service_drive, nome_cliente, nome_servico, arquivo_uploaded):
    try:
        parent_id = '1ZTZ-6-Q46LOQqLTZsxhdefUgsNypNNMS'
        
        meta = {
            'name': f"{datetime.now().strftime('%Y-%m-%d')} - {nome_cliente} - {nome_servico}", 
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        
        folder = service_drive.files().create(body=meta, fields='id, webViewLink').execute()
        folder_id = folder.get('id')

        # --- CORREÇÃO DE IDENTAÇÃO AQUI ---
        if arquivo_uploaded is not None:
            media = MediaIoBaseUpload(arquivo_uploaded, mimetype=arquivo_uploaded.type, resumable=True)
            file_meta = {'name': arquivo_uploaded.name, 'parents': [folder_id]}
            # Adicionamos supportsAllDrives para ajudar com a cota
            service_drive.files().create(
                body=file_meta, 
                media_body=media, 
                fields='id',
                supportsAllDrives=True 
            ).execute()
        
        service_drive.permissions().create(fileId=folder_id, body={'type': 'anyone', 'role': 'writer'}).execute()
        
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

     # 3. Faz o upload do arquivo real (Se houver) 📤
     if arquivo_uploaded is not None:
        media = MediaIoBaseUpload(arquivo_uploaded, mimetype=arquivo_uploaded.type, resumable=True)
        file_meta = {
            'name': arquivo_uploaded.name, 
          'parents': [folder_id] # Aqui garantimos que ele nasça dentro da pasta do cliente
    }
    # Adicionamos 'supportsAllDrives=True' para evitar conflitos de permissão
    service_drive.files().create(
        body=file_meta, 
        media_body=media, 
        fields='id',
        supportsAllDrives=True 
    ).execute()
        
        # 4. Define permissões
        service_drive.permissions().create(fileId=folder_id, body={'type': 'anyone', 'role': 'writer'}).execute()
        
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

        # 3. Faz o upload do arquivo real (Se houver) 📤
        if arquivo_uploaded is not None:
            media = MediaIoBaseUpload(arquivo_uploaded, mimetype=arquivo_uploaded.type, resumable=True)
            file_meta = {'name': arquivo_uploaded.name, 'parents': [folder_id]}
            service_drive.files().create(body=file_meta, media_body=media, fields='id').execute()
        
        # 4. Define permissões
        service_drive.permissions().create(fileId=folder_id, body={'type': 'anyone', 'role': 'writer'}).execute()
        
        return folder.get('webViewLink')
    except Exception as e:
        return f"Erro no Drive: {e}"

def criar_evento_agenda(service_calendar, horario_texto, nome, tel, servico):
    try:
        match = re.search(r"(\d{2}/\d{2}).*às (\d{1,2}):(\d{2})", horario_texto)
        if not match: return "Erro Data"
        dia_mes, hora, minuto = match.group(1), int(match.group(2)), int(match.group(3))
        dt_inicio = datetime.strptime(f"{datetime.now().year}/{dia_mes} {hora}:{minuto}", "%Y/%d/%m %H:%M")
        if dt_inicio < datetime.now(): dt_inicio = dt_inicio.replace(year=datetime.now().year + 1)
        evento = {
            'summary': f'Cálculo: {nome} ({servico})',
            'description': f'Tel: {tel}\nSolicitação via Web App.',
            'start': {'dateTime': dt_inicio.isoformat(), 'timeZone': 'America/Sao_Paulo'},
            'end': {'dateTime': (dt_inicio + timedelta(hours=1)).isoformat(), 'timeZone': 'America/Sao_Paulo'}
        }
        service_calendar.events().insert(calendarId=ID_AGENDA, body=evento).execute()
        return "Confirmado"
    except Exception as e: return f"Erro Agenda: {e}"

def salvar_na_planilha(client_sheets, dados, link):
    try:
        sheet = client_sheets.open(NOME_PLANILHA_GOOGLE).sheet1
        if not sheet.get_all_values(): sheet.append_row(["Data", "Tipo", "Nome", "Contato", "Email", "Horário", "Serviço", "Resumo Cliente", "Análise Técnica (Interna)", "Link Pasta", "Status"])
        sheet.append_row([
            dados['data_hora'], dados['tipo_usuario'], dados['nome'], dados['telefone'], dados['email'],
            dados['melhor_horario'], dados['servico'], dados['analise_cliente'], dados['analise_tecnica'],
            link, dados['status_agenda']
        ])
    except: pass

# --- APLICAÇÃO PRINCIPAL ---
def main():
    # --- 1. O PORTEIRO ---
    if 'encerrado' in st.session_state:
        st.image("https://cdn-icons-png.flaticon.com/512/2643/2643501.png", width=90)
        st.success("✅ **Sessão Finalizada com Sucesso!**")
        st.markdown("### O Consultor Frederico agradece.")
        st.info("Você pode fechar esta aba do navegador agora com segurança.")
        
        if st.button("🔄 Iniciar Nova Sessão"):
            st.session_state.clear()
            st.rerun()
        return

    # --- INÍCIO ---
    st.image("https://cdn-icons-png.flaticon.com/512/2643/2643501.png", width=90)
    st.title("Consultor Frederico - Cálculos Trabalhistas")

    if 'fase' not in st.session_state: st.session_state.fase = 1
    if 'ia_resumo_cliente' not in st.session_state: st.session_state.ia_resumo_cliente = ""
    if 'dados_form' not in st.session_state: st.session_state.dados_form = {}
    if 'conteudo_arquivo' not in st.session_state: st.session_state.conteudo_arquivo = ""
    if 'mensagem_final' not in st.session_state: st.session_state.mensagem_final = ""

    with st.spinner("Conectando..."):
        client_sheets, service_drive, service_calendar = conectar_google()

    # --- FASE 1: COLETA ---
    if st.session_state.fase == 1:
        st.subheader("1. Identificação e Caso")
        tipo_usuario = st.radio("Perfil:", ["Advogado", "Empresa", "Colaborador"], horizontal=True)
        
        col1, col2 = st.columns(2)
        if tipo_usuario == "Empresa":
            nome = col1.text_input("Razão Social")
            cnpj = col2.text_input("CNPJ")
            nome_responsavel = st.text_input("Nome Responsável")
        else:
            nome = col1.text_input("Nome Completo")
            nome_responsavel = nome
            cnpj = ""

        c_tel, c_mail = st.columns(2)
        telefone = c_tel.text_input("WhatsApp (Ex: 11999998888)", key="tel_input", on_change=callback_formatar_telefone)
        email = c_mail.text_input("E-mail")
        
        st.markdown("---")
        if tipo_usuario == "Advogado":
            opcoes = ["Liquidação de Sentença", "Inicial/Estimativa", "Impugnação", "Rescisão", "Horas Extras", "Outros"]
        else:
            opcoes = ["Rescisão", "Horas Extras", "Outros"]

        servico = st.selectbox("Tipo de Cálculo:", opcoes)
        
        c_adm, c_sai = st.columns(2)
        admissao = c_adm.text_input("Admissão")
        saida = c_sai.text_input("Saída")
        salario = st.text_input("Salário Base")
        relato = st.text_area("Resumo da Demanda:", height=100)

        if st.button("💬 Analisar Solicitação"):
            if not nome or not telefone:
                st.warning("Preencha Nome e Telefone.")
            elif tipo_usuario == "Empresa" and not validar_cnpj(cnpj):
                st.error("CNPJ Inválido!")
            else:
                with st.spinner("Analisando relato..."):
                    nome_tratado = formatar_nome_com_titulo(nome_responsavel, tipo_usuario)
                    dados_tecnicos = f"Tipo: {servico}. Perfil: {tipo_usuario}. Salário: {salario}. Período: {admissao} a {saida}."
                    
                    st.session_state.dados_form = {
                        "nome": nome, "nome_resp": nome_responsavel, "tel": telefone, "email": email,
                        "cnpj": cnpj, "tipo": tipo_usuario, "servico": servico, "relato": relato,
                        "tecnico": dados_tecnicos
                    }
                    
                    prompt_cliente = f"""
                    Aja como o Consultor Frederico.
                    O cliente {nome_tratado} relatou: "{relato}".
                    OBJETIVO: Apenas demonstre "Escuta Ativa". Resuma em 1 parágrafo curto que você entendeu o problema central.
                    REGRAS OBRIGATÓRIAS:
                    1. NÃO peça para aguardar.
                    2. NÃO diga frases genéricas como "estou organizando as informações".
                    3. NÃO assine a mensagem (não use [Seu Nome]).
                    4. Seja empático, mas vá direto ao ponto mostrando que entendeu o caso.
                    """
                    st.session_state.ia_resumo_cliente = consultar_ia(prompt_cliente, "Consultor Jurídico", temperatura=0.6)
                    st.session_state.fase = 2
                    st.rerun()

    # --- FASE 2: VALIDAÇÃO ---
    if st.session_state.fase == 2:
        st.subheader("2. Confirmação")
        st.info(st.session_state.ia_resumo_cliente)
        st.markdown("**Eu entendi corretamente sua solicitação?**")
        
        col_sim, col_nao = st.columns(2)
        if col_nao.button("❌ Não (Refazer)"):
            st.session_state.fase = 1
            st.rerun()
        
        if col_sim.button("✅ Sim, está correto"):
            st.session_state.fase = 3
            st.rerun()

# --- FASE 3: COMPLEMENTO ---
    if st.session_state.fase == 3:
        st.subheader("3. Complemento e Documentos")
        st.markdown("Deseja adicionar alguma informação extra ou anexar documentos (Sentença, TRCT, fotos, etc)?")
        
        complemento = st.text_input("Observação Adicional (Opcional):")
        
        # 1. Guardamos o arquivo na memória da sessão e permitimos imagens
        st.session_state.arquivo_anexado = st.file_uploader(
            "Anexar PDF, TXT ou Imagem (Opcional)", 
            type=["pdf", "txt", "jpg", "jpeg", "png"]
        )
        
        if st.button("🔽 Seguir para Agendamento"):
            if complemento:
                st.session_state.dados_form["relato"] += f" [Obs Extra: {complemento}]"
            
            # 2. Verificamos se existe um arquivo na memória
            if st.session_state.arquivo_anexado:
                tipo = st.session_state.arquivo_anexado.type
                
                # Se for imagem, avisamos a IA que é um anexo visual 📸
                if "image" in tipo:
                    st.session_state.conteudo_arquivo = "📸 [O usuário enviou uma imagem/foto. Verifique o anexo no Google Drive para detalhes visuais.]"
                else:
                    st.session_state.conteudo_arquivo = ler_conteudo_arquivo(st.session_state.arquivo_anexado)
                
                st.session_state.dados_form["nome_arquivo"] = st.session_state.arquivo_anexado.name
            else:
                st.session_state.dados_form["nome_arquivo"] = "Nenhum"
                
            st.session_state.fase = 4
            st.rerun()

    # --- FASE 4: AGENDAMENTO ---
    if st.session_state.fase == 4:
        st.subheader("🗓️ Finalizar Agendamento")
        opcoes_horarios = buscar_horarios_livres(service_calendar) if service_calendar else ["Erro Agenda"]
        horario = st.selectbox("Escolha o Horário:", opcoes_horarios)
        
        if st.button("✅ Confirmar Agendamento"):
            with st.spinner("Gerando análise técnica e agendando..."):
                d = st.session_state.dados_form
                tel_formatado = formatar_telefone(d['tel'])
                
                # 3. O prompt agora leva o aviso sobre a imagem se houver
                prompt_tecnico = f"""
                AJA COMO O PERITO FREDERICO. Análise Técnica para uso interno.
                Dados: {d['tecnico']}. Relato Cliente: {d['relato']}.
                Conteúdo Documento Anexo: {st.session_state.conteudo_arquivo}.
                TAREFA: Analise friamente os riscos e verbas. Se houver aviso de imagem, mencione a necessidade de conferência visual manual.
                """
                analise_tecnica_final = consultar_ia(prompt_tecnico, "Perito Judicial Sênior", temperatura=0.2)

                status = criar_evento_agenda(service_calendar, horario, d['nome_resp'], tel_formatado, d['servico'])
                
                # 4. Pegamos o arquivo da "gaveta" para fazer o upload real no Drive
                link_pasta = "Sem anexo"
                arquivo_para_drive = st.session_state.get("arquivo_anexado")
                
                if d.get("nome_arquivo") != "Nenhum":
                    link_pasta = criar_pasta_cliente(service_drive, d['nome'], d['servico'], arquivo_para_drive)

                dados_finais = {
                    "data_hora": datetime.now().strftime("%d/%m %H:%M"),
                    "nome": d['nome'], "telefone": tel_formatado, "email": d['email'],
                    "tipo_usuario": d['tipo'], "servico": d['servico'], "melhor_horario": horario,
                    "analise_cliente": st.session_state.ia_resumo_cliente,
                    "analise_tecnica": analise_tecnica_final,
                    "status_agenda": status
                }
                salvar_na_planilha(client_sheets, dados_finais, link_pasta)
                
                st.session_state.mensagem_final = f"""
                ✅ **Agendamento Confirmado!**
                Muito obrigado pelo contato, {d['nome']}.
                O Consultor Frederico entrará em contato com você exatamente no dia **{horario}** para tratar do seu caso de **{d['servico']}**.
                Prepare seus documentos e até lá!
                """
                st.session_state.fase = 5
                st.rerun()

    # --- FASE 5: TELA DE SUCESSO E DECISÃO ---
    if st.session_state.fase == 5:
        st.balloons()
        st.success(st.session_state.mensagem_final)
        
        st.markdown("---")
        col_novo, col_fim = st.columns(2)
        
        # AGORA ESTES BOTÕES ESTÃO FORA DO BLOCO ANTERIOR E VÃO FUNCIONAR!
        if col_novo.button("🔄 Novo Atendimento"):
            st.session_state.clear()
            st.rerun()
        
        if col_fim.button("🏁 Encerrar Atendimento"):
            st.session_state.encerrado = True
            st.rerun()

if __name__ == "__main__":

    main()




