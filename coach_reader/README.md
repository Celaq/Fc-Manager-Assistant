# 훈련코치 화면 판독기

FC Manager 3.3.4의 훈련코치 판독을 Python으로 옮긴 첫 단계입니다. 저장된 화면 한 장을 읽어 후보 위치, 특성별 점수, 조합 판정, 모집·고용 가격 확인 결과를 JSON으로 출력합니다. 게임 입력과 CP 사용은 기존 Windows 도우미에서 담당합니다.

## Windows에서 실행

Python 3.10 이상이 필요합니다. 이 저장소를 내려받은 뒤 루트 폴더에서 실행합니다.

```powershell
py -m pip install -r coach_reader/requirements.txt
py -m coach_reader "C:\캡처\coach-board.png" --app-dir "C:\FC_Manager_v3.3.4"
```

`--app-dir`에는 기존 도우미의 `FCManager.ps1`이 있는 폴더를 지정합니다. 판독기는 그곳의 `templates`, `.coach-icons`, `coach-preferences.json`을 읽습니다. 이전 4줄과 현재 7줄 설정을 모두 지원합니다. `--icons`와 `--preferences`로 별도 경로를 지정할 수 있습니다.

설치 폴더 없이 실행하면 저장소에 있는 3.3.4 배포본의 버튼·문구 기준 이미지를 사용합니다. 이때도 후보칸과 버튼은 판독할 수 있습니다. 기준 아이콘 48종이 모두 있어야 특성과 조합을 확정합니다. 일부만 있으면 비교 점수와 확보한 개수를 표시합니다.

입력으로 게임 창 테두리를 제외한 16:9 화면이나 도우미가 저장한 720×428 `coach-board.png`를 사용합니다. 고용 확인창이 같은 후보인지 비교할 때는 직전 모집 화면과 위치를 지정합니다.

```powershell
py -m coach_reader "확인창.png" --previous-board "모집화면.png" --card "B 아래"
```

모집 화면에서 `assessment`가 `match`면 `card`와 `combination`이 일치한 위치와 조합 번호입니다. `ambiguous`는 가능한 조합이 남아 판정을 보류했다는 뜻입니다. `refresh_50_issue`가 `null`이면 활성 버튼의 글자, 숫자 50, CP 아이콘을 모두 확인한 것입니다. 이 결과는 해당 프레임의 관측값입니다.

## 회귀 테스트

```powershell
py -m unittest coach_reader.test_reader -v
```
