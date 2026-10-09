# 샌즈전 한글판

[**브라우저에서 플레이하기**](https://godxxy1229.github.io/c2-sans-fight-kr/)

언더테일 팬 시뮬레이터 [Bad Time Simulator](https://github.com/Jcw87/c2-sans-fight)의 한글패치입니다.

## 조작

- 방향키: 이동·메뉴 선택
- Z / Enter: 확인·대사 넘기기
- X / Shift: 취소·대사 즉시 표시

단일·사용자 지정 공격은 X로 종료합니다. PC의 Chrome·Edge에서 플레이를 권장합니다.
사용자 지정 공격은 [CSV 작성 안내](upstream/source/Documentation/README.MD)를 참고하세요.

## 로컬 실행

Python 3.13과 PowerShell 기준입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/build.py
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory dist
```

브라우저에서 `http://127.0.0.1:8765/`를 엽니다.
번역은 `localization/ko.json`에서 수정합니다. 생성 시 `source/`(한글 Construct 2 소스)와 `dist/`(웹 실행판)를 덮어씁니다.
`main`에 push하면 GitHub Actions에서 검증 후 Pages에 배포합니다.

## 출처

- UNDERTALE: [Toby Fox](https://undertale.com/)
- Bad Time Simulator: [Jcw87](https://github.com/Jcw87/c2-sans-fight)
- 번역·글꼴·버튼: 팀 왈도 및 [Minio-KR 한글패치](https://github.com/Minio-KR/undertale-kr-translation)

팬 프로젝트이며, 원작과 참조 자료의 권리는 각 제작자에게 있습니다.
