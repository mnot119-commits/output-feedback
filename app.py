import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import googleapiclient.discovery
import google.generativeai as genai
import pandas as pd
import re
import json
from datetime import datetime
from collections import OrderedDict

# ----------------------------------------------------------------------
# 초기 설정 및 페이지 구성
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="AI 기반 학생 피드백 시스템",
    page_icon="🤖",
    layout="wide",
)

# ----------------------------------------------------------------------
# 구글 API 및 Gemini API 설정 함수
# ----------------------------------------------------------------------
def setup_connections():
    """Google Sheets, Google Docs, Gemini API에 연결합니다."""
    try:
        creds_json = {
            "type": st.secrets["gcp_service_account"]["type"],
            "project_id": st.secrets["gcp_service_account"]["project_id"],
            "private_key_id": st.secrets["gcp_service_account"]["private_key_id"],
            "private_key": st.secrets["gcp_service_account"]["private_key"].replace('\\n', '\n'),
            "client_email": st.secrets["gcp_service_account"]["client_email"],
            "client_id": st.secrets["gcp_service_account"]["client_id"],
            "auth_uri": st.secrets["gcp_service_account"]["auth_uri"],
            "token_uri": st.secrets["gcp_service_account"]["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["gcp_service_account"]["auth_provider_x509_cert_url"],
            "client_x509_cert_url": st.secrets["gcp_service_account"]["client_x509_cert_url"]
        }
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/documents.readonly"
        ]
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        gs = gspread.authorize(creds)
        docs_service = googleapiclient.discovery.build('docs', 'v1', credentials=creds)
        genai.configure(api_key=st.secrets["gemini_api_key"]["api_key"])
        model = genai.GenerativeModel('gemini-1.5-flash')
        return gs, docs_service, model
    except Exception as e:
        st.error(f"API 연결 중 오류가 발생했습니다: {e}")
        st.info("Streamlit Secrets 설정을 확인해주세요. (gcp_service_account, gemini_api_key)")
        return None, None, None

# ----------------------------------------------------------------------
# 데이터베이스 (Google Sheets) 관련 함수
# ----------------------------------------------------------------------
def get_sheet(gs_client, sheet_name):
    """지정된 이름의 구글 시트를 가져옵니다. 없으면 생성합니다."""
    try:
        spreadsheet = gs_client.open_by_key(st.secrets["google_sheet_key"]["sheet_key"])
    except gspread.exceptions.SpreadsheetNotFound:
        st.error("지정된 Key의 구글 스프레드시트를 찾을 수 없습니다.")
        st.info("`secrets.toml` 파일에 올바른 `sheet_key`를 입력했는지 확인해주세요.")
        return None
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        if sheet_name == "users":
            worksheet.append_row(["student_id", "password"])
            worksheet.append_row(["240000", "1234"]) # 예시 학생 데이터
        elif sheet_name == "submissions":
             worksheet.append_row(["student_id", "class_name", "timestamp", "submission_content", "feedback", "record_suggestion"])
    return worksheet

# ----------------------------------------------------------------------
# 인증 및 로그인 관련 함수
# ----------------------------------------------------------------------
def login(users_sheet):
    """로그인 UI를 표시하고 인증을 처리합니다."""
    st.header("🤖 AI 기반 학생 피드백 시스템")
    st.markdown("---")
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
    if not st.session_state['logged_in']:
        with st.form("login_form"):
            student_id = st.text_input("학번")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인")
            if submitted:
                if not student_id or not password:
                    st.warning("학번과 비밀번호를 모두 입력해주세요.")
                    return
                users_df = pd.DataFrame(users_sheet.get_all_records())
                user = users_df[(users_df['student_id'].astype(str) == student_id) & (users_df['password'].astype(str) == password)]
                if not user.empty:
                    st.session_state['logged_in'] = True
                    st.session_state['student_id'] = student_id
                    st.rerun()
                else:
                    st.error("학번 또는 비밀번호가 올바르지 않습니다.")

def logout():
    """로그아웃 처리."""
    if st.sidebar.button("로그아웃"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ----------------------------------------------------------------------
# Google Docs 템플릿 처리 함수 (수정됨)
# ----------------------------------------------------------------------
def get_doc_content(docs_service, document_id):
    """Google Docs 문서 내용을 텍스트로 가져옵니다."""
    try:
        document = docs_service.documents().get(documentId=document_id).execute()
        content = document.get('body').get('content')
        text = ''
        for value in content:
            if 'paragraph' in value:
                elements = value.get('paragraph').get('elements')
                for elem in elements:
                    text_run = elem.get('textRun')
                    if text_run:
                        text += text_run.get('content', '')
                # 각 문단(paragraph)이 끝난 후 줄바꿈 문자를 추가하여 구조를 유지합니다.
                text += '\n'
        return text
    except Exception as e:
        st.error(f"Google Docs 문서를 불러오는 중 오류 발생: {e}")
        st.warning(f"문서 ID '{document_id}'가 올바른지, 서비스 계정에 문서 읽기 권한이 있는지 확인하세요.")
        return None

def parse_template_by_activity(template_text):
    """
    ##을 기준으로 템플릿을 파싱하여 활동별로 내용을 구조화합니다.
    """
    # OrderedDict를 사용하여 활동 순서 보장
    activities = OrderedDict()
    # 정규표현식: {{label:placeholder}} 형식의 입력 필드 패턴
    input_pattern = re.compile(r'\{\{([^:]+):([^}]+)\}\}')
    
    # "## "으로 시작하는 라인을 기준으로 텍스트 분리
    parts = re.split(r'\n## ', '\n' + template_text)
    
    # 첫 번째 빈 부분은 무시
    for part in parts[1:]:
        lines = part.split('\n')
        # 활동 제목은 첫 번째 라인
        activity_title = lines[0].strip()
        # 나머지 내용은 설명 및 입력 필드
        content_text = '\n'.join(lines[1:])
        
        activity_parts = []
        last_end = 0
        
        for match in input_pattern.finditer(content_text):
            start, end = match.span()
            activity_parts.append({'type': 'static', 'content': content_text[last_end:start]})
            
            label = match.group(1).strip()
            placeholder = match.group(2).strip()
            activity_parts.append({'type': 'input', 'label': label, 'placeholder': placeholder})
            
            last_end = end
        
        activity_parts.append({'type': 'static', 'content': content_text[last_end:]})
        activities[activity_title] = activity_parts
        
    return activities

# ----------------------------------------------------------------------
# 데이터 로드 및 저장 함수
# ----------------------------------------------------------------------
def load_previous_submission(submissions_sheet, student_id, class_name):
    """이전 제출 내용을 불러옵니다."""
    try:
        submissions_df = pd.DataFrame(submissions_sheet.get_all_records())
        if submissions_df.empty:
            return {}, "", ""
            
        student_submissions = submissions_df[
            (submissions_df['student_id'].astype(str) == str(student_id)) &
            (submissions_df['class_name'] == class_name)
        ]

        if not student_submissions.empty:
            latest_submission = student_submissions.sort_values(by='timestamp', ascending=False).iloc[0]
            # submission_content가 비어있거나 유효하지 않은 JSON일 경우 처리
            content = latest_submission['submission_content']
            if content and content.strip():
                return json.loads(content), latest_submission['feedback'], latest_submission['record_suggestion']
            else:
                return {}, latest_submission['feedback'], latest_submission['record_suggestion']
    except (json.JSONDecodeError, KeyError) as e:
        st.warning(f"이전 제출 기록을 불러오는 데 실패했습니다. 새롭게 시작합니다. (오류: {e})")
    except Exception as e:
        st.error(f"데이터 로드 중 예상치 못한 오류 발생: {e}")
        
    return {}, "", ""

def save_submission(submissions_sheet, student_id, class_name, submission_content, feedback, record_suggestion):
    """제출 내용을 구글 시트에 저장하거나 업데이트합니다."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    submission_json = json.dumps(submission_content, ensure_ascii=False)
    df = pd.DataFrame(submissions_sheet.get_all_records())
    existing_row = df[(df['student_id'].astype(str) == str(student_id)) & (df['class_name'] == class_name)]
    if not existing_row.empty:
        row_index = existing_row.index[0] + 2
        submissions_sheet.update_cell(row_index, 3, timestamp)
        submissions_sheet.update_cell(row_index, 4, submission_json)
        submissions_sheet.update_cell(row_index, 5, feedback)
        submissions_sheet.update_cell(row_index, 6, record_suggestion)
    else:
        new_row = [student_id, class_name, timestamp, submission_json, feedback, record_suggestion]
        submissions_sheet.append_row(new_row)

# ----------------------------------------------------------------------
# Gemini API 호출 함수 (동일)
# ----------------------------------------------------------------------
def get_ai_feedback(model, class_name, submission_content):
    full_text = f"## 수업: {class_name}\n\n"
    # 제출된 내용만 필터링하여 프롬프트 구성
    submitted_items = {k: v for k, v in submission_content.items() if v and v.strip()}
    if not submitted_items:
        return "제출된 내용이 없어 피드백을 생성할 수 없습니다.", "제출된 내용이 없어 생기부 초안을 생성할 수 없습니다."

    for label, content in submitted_items.items():
        full_text += f"### {label}\n{content}\n\n"
        
    feedback_prompt = f"당신은 고등학생의 학습 활동을 지도하는 친절하고 유능한 교사입니다. 아래 학생이 제출한 내용을 바탕으로, 학생의 성장을 돕는 건설적인 피드백을 작성해주세요.\n\n[피드백 작성 가이드라인]\n1. 칭찬할 점: 학생의 아이디어나 노력에서 긍정적인 부분을 구체적으로 언급하여 동기를 부여해주세요.\n2. 개선할 점: 내용의 논리, 깊이, 창의성 측면에서 보완할 부분을 구체적인 예시와 함께 제안해주세요.\n3. 심화 탐구 제안: 학생의 생각을 더 발전시킬 수 있는 질문이나 관련 자료, 활동을 추천해주세요.\n4. 어조: 학생이 상처받지 않도록, 긍정적이고 격려하는 어조를 사용해주세요.\n5. 형식: 각 항목을 명확하게 구분하여 번호를 붙여 설명해주세요.\n\n[학생 제출 내용]\n{full_text}\n\n자, 이제 위의 가이드라인에 따라 학생을 위한 피드백을 작성해주세요."
    record_prompt = f"당신은 학생의 활동을 관찰하고 핵심 역량을 파악하여 학교생활기록부에 기록하는 대한민국 고등학교 교사입니다. 아래 학생의 제출물을 바탕으로, '과목별 세부능력 및 특기사항'에 기재할 수 있는 구체적이고 객관적인 서술형 초안을 작성해주세요.\n\n[초안 작성 가이드라인]\n1. 핵심 역량 추출: 학생의 글에서 드러나는 비판적 사고력, 창의적 문제 해결 능력, 정보 활용 능력 등 핵심 역량을 구체적인 근거와 함께 서술해주세요. (예: '~~라는 자료를 분석하여 ~~라는 독창적인 대안을 제시하는 등 창의적 문제 해결 능력이 돋보임.')\n2. 과정 중심 서술: 학생이 어떤 고민을 했고, 어떤 과정을 통해 결과물을 만들었는지가 드러나도록 서술해주세요.\n3. 객관적 서술: '매우 뛰어남', '훌륭함'과 같은 주관적인 표현 대신, 학생의 활동과 그 결과를 바탕으로 객관적으로 서술해주세요.\n4. 분량: 1~2개의 문장으로 간결하게 요약해주세요.\n5. 문체: '~함.', '~음.'으로 끝나는 개조식 문체를 사용해주세요.\n\n[학생 제출 내용]\n{full_text}\n\n이제 위의 가이드라인에 따라 생기부 초안을 작성해주세요."
    try:
        with st.spinner("AI가 피드백을 생성하고 있습니다... 잠시만 기다려주세요."):
            feedback_response = model.generate_content(feedback_prompt)
            record_response = model.generate_content(record_prompt)
        return feedback_response.text, record_response.text
    except Exception as e:
        st.error(f"Gemini API 호출 중 오류가 발생했습니다: {e}")
        return "피드백 생성에 실패했습니다.", "생기부 초안 생성에 실패했습니다."

# ----------------------------------------------------------------------
# 메인 애플리케이션 로직 (수정됨)
# ----------------------------------------------------------------------
def main():
    gs, docs_service, model = setup_connections()
    if not all([gs, docs_service, model]): st.stop()

    users_sheet = get_sheet(gs, "users")
    submissions_sheet = get_sheet(gs, "submissions")
    if not users_sheet or not submissions_sheet:
        st.warning("Google Sheets에 연결할 수 없습니다. 설정을 확인해주세요."); st.stop()

    if 'logged_in' not in st.session_state or not st.session_state['logged_in']:
        login(users_sheet)
    else:
        st.sidebar.success(f"{st.session_state['student_id']}님, 환영합니다.")
        logout()
        st.sidebar.markdown("---")

        # CLASS_LIST를 코드에 직접 정의하는 방식으로 되돌립니다.
        CLASS_LIST = {
            "자유 낙하와 수평 방향으로 던진 물체의 운동 비교": "1AnUqkNgFwO6EwX3p3JaVhk8bOT7-TONIdT9sl-lis_U",
            "자유 낙하와 수평 방향으로 던진 물체의 운동 비교": "1AnUqkNgFwO6EwX3p3JaVhk8bOT7-TONIdT9sl-lis_U"
        }
        st.sidebar.info("`app.py`의 `CLASS_LIST`에 수업을 추가하고, Google Docs 템플릿에 `## 활동 제목` 형식으로 내용을 구성해주세요.")
        
        # --- 1단계 사이드바: 수업 선택 ---
        class_name = st.sidebar.radio("수업 선택", list(CLASS_LIST.keys()))
        
        doc_id = CLASS_LIST[class_name]
        template_text = get_doc_content(docs_service, doc_id)

        if not template_text: st.stop()
        
        # 템플릿 파싱 및 세션 상태 초기화
        activities = parse_template_by_activity(template_text)
        if not activities:
            st.warning("템플릿 문서에서 '## '으로 시작하는 활동을 찾을 수 없습니다. 템플릿 형식을 확인해주세요.")
            st.stop()
        
        # 수업이 바뀔 때마다 세션 상태 초기화
        if 'current_class' not in st.session_state or st.session_state.current_class != class_name:
            st.session_state.current_class = class_name
            st.session_state.submission_content, st.session_state.feedback, st.session_state.record = \
                load_previous_submission(submissions_sheet, st.session_state['student_id'], class_name)

        # --- 2단계 사이드바: 활동 선택 ---
        st.sidebar.markdown("---")
        selected_activity_title = st.sidebar.radio("활동 선택", list(activities.keys()))
        
        # 메인 창 구성
        st.header(f"📝 {class_name}")
        st.subheader(selected_activity_title)
        st.markdown("---")

        # 선택된 활동의 내용 표시
        activity_parts = activities[selected_activity_title]
        current_input_label = ""
        for part in activity_parts:
            if part['type'] == 'static':
                st.markdown(part['content'], unsafe_allow_html=True)
            elif part['type'] == 'input':
                current_input_label = part['label']
                # 위젯의 값을 세션 상태와 동기화
                st.session_state.submission_content[current_input_label] = st.text_area(
                    label=part['label'],
                    value=st.session_state.submission_content.get(current_input_label, ""),
                    placeholder=part['placeholder'],
                    height=250,
                    key=f"{class_name}_{current_input_label}" # 위젯 상태 유지를 위한 고유 키
                )
        
        st.markdown("---")
        # 제출 버튼은 모든 활동에 대해 공통으로 사용
        if st.button("전체 내용 저장 및 AI 피드백 받기", type="primary"):
            # 현재 세션 상태에 저장된 모든 내용을 바탕으로 피드백 요청
            if any(st.session_state.submission_content.values()):
                feedback, record_suggestion = get_ai_feedback(model, class_name, st.session_state.submission_content)
                
                # 결과 업데이트 및 저장
                st.session_state.feedback = feedback
                st.session_state.record = record_suggestion
                save_submission(submissions_sheet, st.session_state['student_id'], class_name, st.session_state.submission_content, feedback, record_suggestion)
                
                st.success("저장 및 피드백 생성이 완료되었습니다! 아래에서 결과를 확인하세요.")
            else:
                st.warning("제출할 내용이 없습니다. 하나 이상의 활동을 작성해주세요.")

        # 피드백 및 생기부 기록 표시
        if st.session_state.feedback:
            with st.expander("🤖 AI 피드백 보기", expanded=True):
                st.markdown(st.session_state.feedback)
            with st.expander("📚 생활기록부 기록 예시 보기"):
                st.info("이 내용은 선생님의 기록을 돕기 위한 참고 자료입니다.")
                st.markdown(st.session_state.record)

if __name__ == "__main__":
    main()



