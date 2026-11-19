# AGENTS.md - CloudFPGA (AI-Assisted FPGA & RTL Web Platform)

## 1. Role & Project Goal
너는 AI반도체 및 FPGA 하드웨어 설계(RTL), EDA 툴체인 연동, 그리고 풀스택 웹 개발에 능통한 수석 엔지니어다. 
우리의 목표는 사용자가 웹 브라우저에서 자연어로 Verilog 및 AI 가속기 IP를 생성하고, 서버에서 오픈소스 시뮬레이터(`iverilog`, `yosys`)로 파형 및 합성 리소스를 검증하며, 실제 FPGA 보드와 연동할 수 있는 서비스형 플랫폼(**CloudFPGA**)을 단계별로 구축하는 것이다.

---

## 2. Strict Directory Structure (폴더 구조 규칙)
모든 파일은 반드시 아래의 디렉토리 구조를 엄격히 준수하여 생성한다. 임의로 루트 디렉토리에 파일을 흩뿌리지 마라.

```text
cloudfpga/
├── AGENTS.md                 # 본 명세서
├── backend/                  # Python FastAPI 백엔드 서버
│   ├── main.py               # FastAPI 엔트리포인트 및 CORS 설정
│   ├── requirements.txt      # 의존성 목록 (fastapi, uvicorn, openai, pydantic, numpy 등)
│   ├── routers/              # API 라우터
│   │   ├── rtl_gen.py        # Phase 1: 자연어 -> Verilog/Testbench 생성 & Auto-fix
│   │   ├── simulator.py      # Phase 1: iverilog 실행 및 VCD 파싱
│   │   ├── hardware.py       # Phase 2: XDC 핀 매핑 및 원격 보드 제어
│   │   └── ai_accel.py       # Phase 3: INT8 양자화(.mem) 및 MAC/Systolic IP 생성
│   ├── services/             # 핵심 비즈니스 로직 (CLI 실행, OpenAI 호출, VCD 파서)
│   └── tmp_workspaces/       # 시뮬레이션 임시 작업 공간 (실행 후 자동 삭제 처리)
├── frontend/                 # Next.js (TypeScript) 웹 애플리케이션
│   ├── package.json
│   └── src/
│       ├── app/              # Next.js App Router 페이지
│       └── components/       # MonacoEditor, WaveformViewer, VirtualBoard, IPWizard
└── hardware_assets/          # 하드웨어 템플릿 및 보드 데이터
    ├── boards/               # 보드별 마스터 핀맵 JSON (예: basys3.json, arty_a7.json)
    ├── ip_templates/         # Parameterized MAC, Systolic Array Verilog 템플릿
    └── hw_bridge.py          # Phase 2: 로컬 PC와 FPGA 보드를 연결하는 클라이언트 스크립트
```

---

## 3. Tech Stack & Coding Constraints (기술 스택 및 제약 조건)
- **Backend:** Python 3.10+, FastAPI, Pydantic v2, OpenAI Python SDK (`>=1.0.0`).
- **Frontend:** Next.js 14+ (App Router), TypeScript, Tailwind CSS, `@monaco-editor/react`.
- **EDA CLI Tools (Linux/WSL2):** `iverilog`, `vvp`, `yosys` (합성 및 리소스 리포트 추출용).
- **보안 및 안정성 규칙:**
  - 외부 CLI(`iverilog`, `vvp`, `yosys`) 호출 시 반드시 `subprocess.run(..., timeout=15, capture_output=True, text=True)`를 사용하여 무한 루프(예: `$finish` 누락된 테스트벤치)로 인한 서버 다운을 방지한다.
  - 각 시뮬레이션 요청마다 `uuid4()` 기반의 독립된 임시 폴더를 생성하고, 완료 즉시 `shutil.rmtree()`로 정리한다.
- **Verilog 코드 생성 시스템 프롬프트 강제 규칙:**
  1. `design_code`는 반드시 IEEE 1364-2005 표준을 따르는 합성 가능(Synthesizable)한 코드여야 한다 (`initial`, `#delay` 사용 금지).
  2. `testbench_code`의 최상단 모듈 이름은 반드시 `tb`로 고정하며, 파형 추출을 위해 `initial begin $dumpfile("dump.vcd"); $dumpvars(0, tb); ... #1000 $finish; end` 구문을 반드시 포함해야 한다.

---

## 4. API Data Contracts (입출력 JSON 스키마 고정)
프론트엔드와 백엔드 간의 통신 규격은 아래 JSON 형식을 절대 벗어나지 않는다.

### [Phase 1] `POST /api/rtl/generate`
- **Request:** `{ "prompt": "4-bit synchronous up counter with active-low reset", "module_name": "up_counter" }`
- **Response:** `{ "design_code": "string", "testbench_code": "string", "explanation": "string" }`

### [Phase 1] `POST /api/rtl/simulate`
- **Request:** `{ "design_code": "string", "testbench_code": "string" }`
- **Response:** 
  ```json
  {
    "success": true,
    "compile_logs": "string",
    "sim_logs": "string",
    "vcd_raw": "string",
    "signals": [
      { "name": "clk", "wave": "010101..." },
      { "name": "count[3:0]", "wave": ["0", "1", "2", "..."] }
    ]
  }
  ```

### [Phase 1] `POST /api/rtl/autofix`
- **Request:** `{ "design_code": "string", "testbench_code": "string", "error_logs": "string" }`
- **Response:** `{ "fixed_design_code": "string", "fixed_testbench_code": "string", "fix_summary": "string" }`

### [Phase 2] `POST /api/hw/generate-xdc`
- **Request:** `{ "design_code": "string", "board": "basys3" }`
- **Response:** `{ "xdc_content": "string", "mapped_ports": [{ "port": "clk", "pin": "W5" }] }`

### [Phase 3] `POST /api/accel/generate-ip`
- **Request:** `{ "array_size": 4, "data_width": 8, "acc_width": 32, "weights": [[1, 2], [3, 4]] }`
- **Response:** `{ "verilog_ip": "string", "weight_mem_hex": "string", "golden_testbench": "string" }`

---

## 5. Agent Self-Verification Rules (코덱스 자가 검증 규칙)
코드를 작성하거나 수정한 뒤에는 사용자에게 완료 보고를 하기 전에 반드시 터미널을 열어 아래 검증 절차를 스스로 수행하라.
1. **Backend 검증:** `uvicorn` 서버 실행 테스트 및 샘플 Verilog 코드(예: 2-input AND 게이트)를 만들어 실제 `iverilog`와 `vvp`가 에러 없이 VCD 파일을 생성하는지 CLI로 직접 테스트한다.
2. **Frontend 검증:** `npm run build` 또는 타입 체크(`npx tsc --noEmit`)를 실행하여 TypeScript 컴파일 에러가 0개임을 확인한다.
3. 만약 로컬 환경에 `iverilog`가 설치되어 있지 않다면, 실행을 멈추고 사용자에게 설치 명령어(`sudo apt-get install -y iverilog yosys`) 안내를 출력하라.

---

## 6. Execution Roadmap (단계별 지시 가이드)
사용자가 별도의 지시를 내리기 전까지는 전체를 한 번에 구현하지 말고, 아래 순서에 맞춰 단계별로 질문하고 구현한다.
- **Step 1 (Init & Phase 1 Backend):** 폴더 구조 생성, `backend` 환경 구축, OpenAI 연동 및 `iverilog` 시뮬레이션/파형 파싱 API 구현 및 단위 테스트.
- **Step 2 (Phase 1 Frontend):** Next.js 초기 세팅, Monaco Editor 코드 편집기, 시뮬레이션 버튼, 파형 뷰어 UI 구현 및 백엔드 연동.
- **Step 3 (Phase 2 Hardware Bridge):** `basys3.json` 핀맵 데이터 구축, `.xdc` 자동 생성기, 가상 스위치/LED 웹 UI 및 `hw_bridge.py` 구현.
- **Step 4 (Phase 3 AI Accelerator):** INT8 양자화 `.mem` 생성기 및 Parameterized Systolic Array(행렬 곱셈기) Verilog IP 생성 위자드 구현.
