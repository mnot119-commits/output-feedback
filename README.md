## 🤖 AI 기반 학생 피드백 및 학생부 기록 보조 시스템
고등학교 수업 환경에 맞춰 개발된 Streamlit 웹 애플리케이션입니다. 학생들이 제출한 학습 산출물에 대해 AI가 자동으로 피드백을 제공하고, 그 내용을 바탕으로 교사가 생활기록부의 '과목별 세부능력 및 특기사항'을 작성하는 데 도움을 주는 것을 목표로 합니다.
## ✨ 핵심 기능
### Google Docs 템플릿 연동
교사가 미리 만들어 둔 구글 문서 양식에 학생들이 내용을 채워 제출할 수 있습니다.
### 간편한 로그인
학번과 비밀번호 기반의 간단한 로그인 시스템을 통해 학생별 제출 이력을 관리합니다.
### Gemini API 기반 AI 피드백
제출된 내용에 대해 구체적인 칭찬, 개선점, 심화 탐구 제안이 담긴 AI 피드백을 생성합니다.
### 생활기록부 초안 생성
학생의 제출물에서 핵심 역량을 추출하여 생활기록부 기재를 위한 객관적인 서술형 초안을 제공합니다.
### Google Sheets 데이터베이스
모든 로그인 정보와 제출 내용은 구글 시트에 안전하게 기록 및 관리됩니다.
### 확장 가능한 수업 목록
사이드바 메뉴에 새로운 수업 템플릿을 손쉽게 추가할 수 있습니다.

## 🛠️ 시스템 구성도
[학생] <--> [Streamlit 앱]  <--> [Gemini API]  
|  
+------> [Google Docs (템플릿)]  
|  
+------> [Google Sheets (DB)]

## 🚀 배포 및 설정 가이드
이 애플리케이션을 Streamlit Cloud에 배포하기 위한 단계별 안내입니다.
### 1. 사전 준비물
Google Cloud Platform (GCP) 계정, Google AI Studio 에서 발급받은 Gemini API 키, GitHub 계정
### 2. Google Cloud 및 Workspace 설정
#### 가. Google Cloud Platform (GCP) 설정
1. 새 프로젝트를 생성하고, Google Sheets API, Google Drive API, Google Docs API를 활성화합니다.
2. IAM 및 관리자 메뉴에서 서비스 계정을 생성하고 ```편집자``` 역할을 부여합니다.
3. 생성된 서비스 계정의 키 탭에서 ```JSON``` 타입의 새 키를 발급받아 PC에 다운로드합니다. 
4. 이 파일의 내용은 잠시 후 사용됩니다.
#### 나. Google Sheets 및 Docs 설정
1. 데이터를 저장할 새 Google 스프레드시트를 생성합니다.
2. 생성한 시트의 URL에서 /d/와 /edit 사이의 긴 문자열(스프레드시트 Key)을 복사해 둡니다.
3. [파일] > [공유] 메뉴에서 위에서 다운로드한 JSON 파일 안의 client_email 주소를 추가하고 편집자 권한을 부여합니다.
4. 수업 활동지 템플릿으로 사용할 Google 문서를 만듭니다. 
5. 학생이 입력할 부분은 {{레이블:안내 문구}} 형식으로 작성합니다.
6. 템플릿 문서 역시 서비스 계정 이메일에 뷰어 권한으로 공유하고, 문서의 ID(URL에서 Key 부분)를 복사해 둡니다.
### 3. GitHub 저장소 준비
#### 새로운 GitHub 저장소를 Public으로 생성합니다.
1. 이 저장소에 app.py와 requirements.txt 두 파일을 업로드합니다.
2. app.py: 제공된 Streamlit 애플리케이션 코드
3. requirements.txt: 아래 내용이 포함된 텍스트 파일
>streamlit  
gspread  
google-auth-oauthlib  
google-auth-httplib2  
google-api-python-client  
google.generativeai  
pandas  
4. Streamlit Cloud 배포Streamlit Community Cloud에 GitHub 계정으로 로그인합니다.
5. New app 버튼을 클릭하여 위에서 생성한 GitHub 저장소를 선택합니다.
6. Advanced settings...를 클릭하여 Secrets 설정에 아래 내용을 붙여넣고, 각 항목을 선생님의 정보로 채워줍니다. (GCP에서 받은 JSON 파일 내용, 시트 Key, Gemini API 키 필요)
> [gcp_service_account]  
type = "service_account"  
project_id = "..."  
 ... (JSON 파일 내용 전체) ...  
client_x509_cert_url = "..."    
[google_sheet_key]  
sheet_key = "여기에_복사해 둔_구글_시트_Key를_입력"  
[gemini_api_key]  
api_key = "여기에_발급받은_Gemini_API_Key를_입력"  

#### Deploy! 버튼을 누르면 배포가 시작됩니다.
## 📝 사용 방법
### 교사용
#### 학생 등록
1. 공유된 구글 시트의 users 탭에 학생들의 학번과 초기 비밀번호를 입력합니다.
2. 수업 추가: app.py 파일의 CLASS_LIST 딕셔너리에 "수업 이름": "구글 문서 ID" 형식으로 새 수업을 추가하고 저장하면 앱에 자동 반영됩니다.
#### 학생용
1. 배포된 앱 주소로 접속합니다.
2. 부여받은 학번과 비밀번호로 로그인합니다.
3. 왼쪽 사이드바에서 수업을 선택하고, 양식에 맞춰 내용을 작성합니다.
4. 제출 및 AI 피드백 받기 버튼을 누르면 AI가 생성한 피드백을 확인할 수 있습니다.  

이 프로젝트가 선생님의 수업과 학생들의 성장에 도움이 되기를 바랍니다.