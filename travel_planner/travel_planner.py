import requests  # 파일 맨 위에 추가
import argparse       # 명령줄 옵션(--date)을 받기 위한 도구
from datetime import datetime  # 날짜 형식 검증용
import os                          # 환경변수 읽기용
from dotenv import load_dotenv     # .env 파일 로드용
from google import genai   # Gemini 사용
import json                            # AI 응답(JSON) 처리용

def parse_date(date_str):
    """
    날짜 문자열이 'YYYY-MM-DD' 형식인지 검사한다.
    올바르면 날짜 문자열을 그대로 돌려주고,
    틀리면 에러를 발생시킨다.
    """
    try:
        # strptime: 문자열을 날짜로 변환 시도 -> 형식이 틀리면 에러 발생
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        # argparse가 이해할 수 있는 에러로 변환
        raise argparse.ArgumentTypeError(
            f"날짜 형식이 올바르지 않습니다: '{date_str}' (예: 2025-05-10)"
        )

def load_api_keys():
    """
    .env 파일에서 API 키를 읽어온다.
    키가 없으면 명확한 에러 메시지를 보여주고 프로그램을 멈춘다.
    """
    load_dotenv()  # .env 파일 내용을 환경변수로 불러옴

    gemini_key = os.getenv("GEMINI_API_KEY")
    kakao_key = os.getenv("KAKAO_API_KEY")

    # 키가 없으면 미리 알려주기 (나중에 헤매지 않도록!)
    if not gemini_key:
        raise SystemExit("❌ GEMINI_API_KEY가 .env에 없습니다. 확인해주세요.")
    if not kakao_key:
        raise SystemExit("❌ KAKAO_API_KEY가 .env에 없습니다. 확인해주세요.")

    return gemini_key, kakao_key

def get_travel_recommendations(gemini_key, date):
    """
    Gemini에게 날짜를 주고 국내 여행지 3곳을 추천받는다.
    JSON 파싱 실패 시 최대 1회 재시도한다.
    결과는 JSON 형태(리스트)로 돌려준다.
    """
    # 1) Gemini 준비
    client = genai.Client(api_key=gemini_key)
    #model = genai.GenerativeModel("gemini-3.6-flash")

    # 2) AI에게 보낼 요청문(프롬프트)
    prompt = f"""
당신은 국내 여행 전문가입니다.
{date}에 방문하기 좋은 대한민국 국내 여행지 3곳을 추천해주세요.

반드시 아래 JSON 형식으로만 답변하세요 (설명 없이):
{{
  "recommendations": [
    {{
      "recommended_city": "도시명",
      "weather": "{date} 시기의 일반적인 날씨 요약",
      "events": ["행사/축제 1", "행사/축제 2"],
      "reason": "추천 근거 2~4문장"
    }},
    ... (총 3개)
  ]
}}
"""
    # 최대 2번 시도 (첫 시도 + 재시도 1회)
    for attempt in range(2):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",   # 여기에 모델명!
                contents=prompt
            )
            text = response.text.strip()
            # AI가 가끔 ```json ... ``` 로 감싸므로 제거
            text = text.replace("```json", "").replace("```", "").strip()
            places = json.loads(text)
            return places["recommendations"]

        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ JSON 파싱 실패 (시도 {attempt + 1}/2): {e}")
            if attempt == 0:
                print("   → 다시 시도합니다...")
                # 재시도 시엔 '순수 JSON만' 요청하도록 프롬프트 강화
                prompt += "\n\n주의: 코드블록(```)이나 설명 없이 순수 JSON만 출력하세요."
            else:
                # 재시도도 실패하면 에러를 위로 전달
                raise RuntimeError("Gemini 추천 JSON 파싱에 2번 실패했습니다.")

def search_restaurants(kakao_key, city, count=5, errors=None):
    """
    Kakao Local API로 '{도시} 맛집'을 검색해서 최대 5곳을 리스트로 반환한다.
    검색 실패나 결과 0건이면 빈 리스트 []를 반환한다.
    """
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    params = {
        "query": f"{city} 맛집",   # ⭐ "보성 맛집" 형태로 검색
        "size": count              # ⭐ 5곳 요청
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()  # 4xx, 5xx 응답이면 에러 발생
        data = response.json()
        documents = data.get("documents", [])  # 검색 결과 목록

    # 미션 요구 필드만 골라서 정리
        restaurants = []
        for place in documents:
            restaurants.append({
                "name": place.get("place_name"),
                "address": place.get("road_address_name") or place.get("address_name"),
                "category": place.get("category_name"),
                "url": place.get("place_url"),
                "x": place.get("x"),
                "y": place.get("y"),
            })
        return restaurants  

    except Exception as e:
        # 실패해도 프로그램은 멈추지 않고 "데이터 없음"으로 진행
        msg = f"[{city}] 맛집 검색 실패: {e}"
        print(f"⚠️ {msg}")
        if errors is not None:
            errors.append(msg)
        return []  # 빈 리스트 반환
      
def generate_report(gemini_key, recommendation, restaurants):
    """
    1차 추천 정보 + 맛집 리스트를 Gemini에게 주고
    최종 여행 리포트를 Markdown 텍스트로 생성한다.
    """
    client = genai.Client(api_key=gemini_key)
    #model = genai.GenerativeModel("gemini-3.6-flash")

    # 맛집 목록을 문자열로 정리 (AI가 읽기 쉽게)
    if restaurants:
        food_text = "\n".join(
            [f"- {r['name']} ({r['category']}) / {r['address']}" for r in restaurants]
        )
    else:
        food_text = "데이터 없음"

    prompt = f"""
당신은 여행 플래너입니다. 아래 정보를 바탕으로 여행 리포트를 
Markdown 형식으로 작성해주세요.

[추천 도시] {recommendation['recommended_city']}
[날씨] {recommendation['weather']}
[행사/축제] {', '.join(recommendation['events'])}
[추천 이유] {recommendation['reason']}
[맛집 목록]
{food_text}

리포트에는 반드시 아래 항목을 포함하세요:
1. 추천 지역 + 추천 이유 요약
2. 날씨 요약
3. 행사/축제 목록
4. 맛집 리스트 (데이터 없으면 "데이터 없음"으로 표기)
5. 1일 일정 제안 (오전/오후/저녁)

Markdown 형식(제목 #, 목록 - 등)을 활용해 보기 좋게 작성하세요.
"""

    response = client.models.generate_content(
    model="gemini-3.6-flash",   # 여기에 모델명!
    contents=prompt
)
    return response.text

def save_report(report_text, city, date):
    """
    리포트 텍스트를 results/ 폴더에 Markdown 파일로 저장한다.
    파일명: results/여행리포트_보성_2025-05-10.md
    """
    # results 폴더가 없으면 자동 생성
    os.makedirs("results", exist_ok=True)

    # 파일 경로 만들기
    filename = f"results/여행리포트_{city}_{date}.md"

    # 파일 쓰기 (utf-8로 저장해야 한글 안 깨져요!)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(report_text)

    return filename

def save_json(all_data, date):
    """
    원본 데이터(추천 + 맛집 + 오류)를 results/ 폴더에 JSON으로 저장한다.
    파일명: results/원본데이터_2025-05-10.json
    """
    os.makedirs("results", exist_ok=True)   # results 폴더 없으면 생성
    filename = f"results/원본데이터_{date}.json"

    # ensure_ascii=False → 한글 안 깨짐 / indent=2 → 보기 좋게 들여쓰기
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    return filename


def main():
    # 1) argparse 준비
    parser = argparse.ArgumentParser(
        description="국내 여행지 추천 프로그램 (Gemini + Kakao)"
    )
    # 2) --date 옵션 추가 (필수, 검증 함수 연결)
    parser.add_argument(
        "--date",
        required=True,          # 반드시 입력해야 함
        type=parse_date,        # 위에서 만든 검증 함수 사용
        help='여행 날짜 (형식: YYYY-MM-DD, 예: 2025-05-10)'
    )

    # 3) 입력값 읽기
    args = parser.parse_args()

    # 4) 잘 들어왔는지 확인용 출력
    print(f"입력된 날짜: {args.date}")

    # 5) API 키 로드
    gemini_key, kakao_key = load_api_keys()
    print("✅ API 키 로드 완료!")

    # 6) 오류를 모아둘 리스트
    errors = []

    # 7) Gemini 추천 받기 (JSON 파싱 실패 시 재시도 포함)
    try:
        cities = get_travel_recommendations(gemini_key, args.date)
        print(f"\n✨ 총 {len(cities)}개 도시를 추천받았어요!")
    except Exception as e:
        # 추천 자체가 실패하면 더 진행할 수 없으므로 종료
        print(f"❌ 여행지 추천 실패: {e}")
        raise SystemExit(1)

    # 8) 원본 데이터를 담을 구조 (JSON 저장용)
    all_data = {
        "date": args.date,
        "recommendations": cities,   # 1차 추천 JSON
        "restaurants_by_city": {},   # 도시별 맛집 결과
        "errors": errors             # 오류 요약
    }

    saved_reports = []  # 저장된 리포트 경로 모음

    # ⭐⭐ 7) 도시 3개를 하나씩 반복 처리 ⭐⭐
    for idx, recommendation in enumerate(cities, start=1):
        city = recommendation["recommended_city"]

        # ── 추천 정보 출력 ──
        print("\n" + "=" * 40)
        print(f"🏙️ [{idx}/{len(cities)}] 추천 도시: {city}")
        print("=" * 40)
        print(f"날씨: {recommendation['weather']}")
        print(f"행사: {recommendation['events']}")
        print(f"이유: {recommendation['reason']}")

        # --- STEP 2: 맛집 검색 (errors 전달!) ---
        restaurants = search_restaurants(kakao_key, city, errors=errors)
        # 도시별 맛집 결과를 원본 데이터에 저장
        all_data["restaurants_by_city"][city] = restaurants

        if restaurants:
            print(f"\n🍽️ '{city}' 맛집 {len(restaurants)}곳 검색 완료!")
            for i, r in enumerate(restaurants, 1):
                print(f"{i}. {r['name']} ({r['category']})")
                print(f"   주소: {r['address']}")
        else:
            print(f"\n🍽️ '{city}' 맛집: 데이터 없음")

        # --- STEP 3: 리포트 생성 (실패해도 계속) ---
        print(f"\n📄 '{city}' 리포트 생성 중...")
        try:
            report = generate_report(gemini_key, recommendation, restaurants)
            saved_path = save_report(report, city, args.date)
            saved_reports.append(saved_path)
            print(f"💾 '{city}' 리포트 저장 완료! → {saved_path}")
        except Exception as e:
            msg = f"[{city}] 리포트 생성 실패: {e}"
            print(f"⚠️ {msg}")
            errors.append(msg)
            
    # 8) 원본 데이터 JSON 저장
    json_path = save_json(all_data, args.date)
    print(f"\n📦 원본 데이터 저장 완료! → {json_path}")

    # 9) 전체 완료 메시지
    print("\n" + "🎉" * 20)
    print(f"모든 작업 완료! 리포트 {len(saved_reports)}개 + JSON 1개 저장됨")
    if errors:
        print(f"⚠️ 처리 중 {len(errors)}건의 오류가 있었어요 (JSON의 errors 참고)")
    print("🎉" * 20)


if __name__ == "__main__":
    main()

