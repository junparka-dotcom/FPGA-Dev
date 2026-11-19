# CloudFPGA 개발 노트

CloudFPGA는 자연어로 Verilog 설계와 테스트벤치를 생성하고, 서버에서 시뮬레이션해 파형을 확인하는 FPGA 개발 플랫폼입니다. 구현 범위와 API 계약은 [AGENTS.md](AGENTS.md)를 기준으로 관리합니다.

## 진행 상황

| 단계 | 상태 | 작업 범위 |
| --- | --- | --- |
| 1. Phase 1 백엔드 | 구현 및 로컬 검증 완료 | 디렉터리 구조, FastAPI, OpenAI 연동, Icarus Verilog 실행, VCD 파싱, 테스트 |
| 2. Phase 1 프론트엔드 | 예정 | Next.js, Monaco 편집기, 시뮬레이션 요청, 파형 뷰어 |
| 3. FPGA 보드 연동 | 예정 | 핀맵, XDC 생성, 가상 보드, 하드웨어 브리지 |
| 4. AI 가속기 IP | 예정 | INT8 가중치 파일, MAC 및 Systolic Array IP |

## Step 1 코드 읽는 순서

1. [backend/main.py](backend/main.py)에서 FastAPI 앱이 라우터를 등록하는 방식과 개발 서버용 CORS 설정을 살펴봅니다.
2. [backend/routers/rtl_gen.py](backend/routers/rtl_gen.py)에서 생성과 자동 수정의 요청·응답 모델을 확인합니다. [backend/services/rtl_generator.py](backend/services/rtl_generator.py)는 이 모델을 OpenAI Responses API의 구조화 출력 스키마로 사용합니다. 시스템 프롬프트에는 합성 가능한 Verilog-2005와 `tb`, `dump.vcd`, `$finish` 규칙이 들어갑니다. [OpenAI 구조화 출력 문서](https://developers.openai.com/api/docs/guides/structured-outputs)
3. [backend/routers/simulator.py](backend/routers/simulator.py)를 따라가면 [backend/services/simulation.py](backend/services/simulation.py)에 도달합니다. 요청마다 UUID 작업 폴더에 소스를 쓰고, `iverilog`로 컴파일한 뒤 `vvp`로 실행합니다. 두 프로세스에 각각 15초 제한을 걸고, 성공 여부와 상관없이 실행이 끝나면 작업 폴더를 삭제합니다.
4. [backend/services/vcd.py](backend/services/vcd.py)에서 각 VCD 시각의 변경을 반영한 뒤 모든 신호를 한 번씩 기록합니다. 단일 비트 파형은 문자열, 다중 비트 파형은 배열로 내려갑니다. 확정된 버스 값은 10진 문자열, X와 Z가 있는 값은 비트 문자열로 표현합니다. 실제 시각과 타임스케일은 원본 `vcd_raw`에 들어 있습니다.
5. [backend/tests/test_phase1.py](backend/tests/test_phase1.py)에서 AND 게이트의 네 입력 조합, 컴파일·실행 오류, 타임아웃 및 OpenAI 요청 규칙을 확인합니다.

## 로컬에서 실행하기

WSL Ubuntu 또는 Linux에서 Python 3.10 이상을 사용하며, 아래 명령은 이 README가 있는 `cloudfpga` 디렉터리에서 실행합니다.

```bash
sudo apt-get install -y iverilog yosys python3-venv
python3 -m venv "$HOME/.local/share/cloudfpga/venv"
source "$HOME/.local/share/cloudfpga/venv/bin/activate"
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Windows에서 프로젝트가 `/mnt/c`에 있어도 가상환경은 위 명령처럼 WSL 홈에 두면 패키지 설치가 더 빠릅니다.

서버가 실행되면 [Swagger UI](http://127.0.0.1:8000/docs)에서 각 요청을 직접 보낼 수 있습니다. `generate`와 `autofix`를 호출하려면 서버를 시작하는 셸에 `OPENAI_API_KEY`를 설정합니다. 모델은 `OPENAI_MODEL`로 바꿀 수 있으며 기본값은 `gpt-4o-mini`입니다. 브라우저 개발 서버 주소가 다르면 `CORS_ORIGINS`에 쉼표로 구분해 지정합니다.

| API | 요청에 넣을 값 | 확인할 결과 |
| --- | --- | --- |
| `POST /api/rtl/generate` | 자연어 `prompt`, `module_name` | 설계, 테스트벤치, 설명 |
| `POST /api/rtl/simulate` | `design_code`, `testbench_code` | 성공 여부, 로그, 원본 VCD와 신호별 파형 |
| `POST /api/rtl/autofix` | 기존 소스 두 개, 컴파일 또는 실행의 `error_logs` | 수정된 설계와 테스트벤치, 수정 요약 |

Swagger UI에서 `simulate`를 사용해 보려면 테스트 파일의 `AND_DESIGN`과 `AND_TESTBENCH`를 요청에 붙여 넣습니다. 출력 `y`는 입력 `a`와 `b`가 모두 1일 때 1로 바뀝니다. 설계를 일부러 잘못 고친 뒤 다시 실행하면 `compile_logs`에서 진단을 읽고 `autofix`의 `error_logs`에 전달하는 흐름을 연습할 수 있습니다.

현재 시뮬레이터는 독립 실행 가능한 Verilog 소스를 받습니다. 허용된 전처리 지시문은 `timescale`, `default_nettype`, `resetall`이며, 파일 출력은 고정된 `dump.vcd` 경로를 사용합니다. API는 최대 4 MiB의 VCD를 읽고 신호와 시각의 조합을 최대 25만 개까지 파싱합니다. 서버 예제는 로컬 학습용으로 바인딩합니다.

## 검증

```bash
python -m unittest discover -s backend/tests -v
```

두 CLI가 각각 무슨 일을 하는지 보려면 별도의 임시 폴더에 `AND_DESIGN`과 `AND_TESTBENCH`를 `design.v`와 `testbench.v`로 저장한 뒤 그 폴더에서 실행합니다.

```bash
iverilog -g2005 -s tb -o sim.vvp design.v testbench.v
vvp -n sim.vvp
head -n 25 dump.vcd
```

시뮬레이션 통합 테스트에는 실제 `iverilog`와 `vvp`가 필요합니다. OpenAI 관련 테스트는 모의 응답을 사용하므로 API 키 없이 실행할 수 있습니다.

### 2026-11-19 검증 결과

| 항목 | 결과 |
| --- | --- |
| 백엔드 테스트 | WSL Ubuntu에서 13개 통과 |
| AND 게이트 CLI 테스트 | 실제 `iverilog`와 `vvp` 모두 종료 코드 0, `dump.vcd` 생성 |
| Uvicorn HTTP 테스트 | 세 API 등록 확인, `simulate`가 AND 게이트 파형 `y = 00011` 반환 |
| 타임아웃 및 작업 폴더 정리 | 종료 없는 테스트벤치를 약 15초 후 중단, UUID 작업 폴더 삭제 확인 |
| OpenAI | 생성·자동 수정 요청과 응답 규격은 모의 응답으로 검증, 실 API 호출은 키 설정 후 확인 |
| 프론트엔드 빌드 | Next.js 초기 설정과 함께 Step 2에서 검증 |
