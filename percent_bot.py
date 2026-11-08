#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
percent_bot.py - 네이버 쇼핑 '농심 신라면 봉지 20개입' 최저가 알림 봇 (카카오톡 알림 기능 포함)
"""

import os
import sys
import json
import re
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Dict, Any, Optional, Tuple

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

TARGET_PRICE = 15000
DEFAULT_QUERY = "농심 신라면 봉지 20개입"

EXCLUDE_KEYWORDS = [
    "건면", "블랙", "골드", "더레드", "볶음", "사발", "컵", "두부",
    "똠얌", "마라", "비빔", "투움바", "안성탕면", "너구리", "짜파게티",
    "진라면", "삼양라면", "오징어짬뽕", "육개장", "열라면", "불닭"
]

SPECIAL_PRICE_MSG = "🚨 [특가 발생] 목표 가격 이하입니다!"
MAINTAIN_PRICE_MSG = "ℹ️ [유지] 아직 목표 가격보다 비쌉니다."


# =====================================================================
# 카카오톡 인증 및 '나에게 보내기' 관련 함수
# =====================================================================

def get_env_file_path() -> str:
    """프로젝트 폴더 내 .env 파일의 절대 경로를 반환합니다."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    primary_path = os.path.join(base_dir, ".env")
    if os.path.exists(primary_path):
        return primary_path
    fallback_path = r"c:\ag20_project\percent\.env"
    return fallback_path


def load_env(env_path: Optional[str] = None) -> Dict[str, str]:
    """.env 파일에서 환경변수를 읽어 딕셔너리로 반환합니다 (코드 내 하드코딩 금지)."""
    if env_path is None:
        env_path = get_env_file_path()

    env_vars: Dict[str, str] = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars


def update_env_refresh_token(new_refresh_token: str, env_path: Optional[str] = None):
    """.env 파일의 KAKAO_REFRESH_TOKEN 항목을 갱신합니다."""
    if env_path is None:
        env_path = get_env_file_path()

    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("KAKAO_REFRESH_TOKEN="):
            new_lines.append(f"KAKAO_REFRESH_TOKEN={new_refresh_token}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        new_lines.append(f"KAKAO_REFRESH_TOKEN={new_refresh_token}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def get_kakao_access_token(env_path: Optional[str] = None) -> str:
    """.env의 KAKAO_REFRESH_TOKEN을 사용하여 카카오 인증 서버로부터 Access Token을 갱신 발급받습니다."""
    env = load_env(env_path)
    client_id = env.get("KAKAO_REST_API_KEY")
    client_secret = env.get("KAKAO_CLIENT_SECRET")
    refresh_token = env.get("KAKAO_REFRESH_TOKEN")

    if not client_id:
        raise ValueError(".env 파일에 KAKAO_REST_API_KEY가 설정되어 있지 않습니다.")
    if not refresh_token:
        raise ValueError(".env 파일에 KAKAO_REFRESH_TOKEN이 설정되어 있지 않습니다.")

    token_url = "https://kauth.kakao.com/oauth/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "User-Agent": "curl/8.0"
    }
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(token_url, data=encoded_data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            access_token = res.get("access_token")
            if not access_token:
                raise RuntimeError(f"토큰 갱신 응답에 access_token이 없습니다: {res}")

            # 리프레시 토큰이 함께 갱신된 경우 저장
            new_refresh = res.get("refresh_token")
            if new_refresh and new_refresh != refresh_token:
                update_env_refresh_token(new_refresh, env_path)

            return access_token
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"카카오 토큰 갱신 HTTP {e.code} 에러: {err_body}")
    except Exception as e:
        raise RuntimeError(f"카카오 토큰 갱신 네트워크 오류: {e}")


def send_kakao_memo(item: Dict[str, Any], env_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    1위 특가 상품 정보를 카카오톡 '나에게 보내기'로 전송합니다.
    - 상품명, 가격, 쇼핑몰 이름, 리뷰 수 포함
    - '구매하러 가기' 버튼으로 상품 링크 연결
    """
    try:
        access_token = get_kakao_access_token(env_path)
    except Exception as e:
        return False, f"카카오 Access Token 갱신 실패: {e}"

    send_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "User-Agent": "curl/8.0"
    }

    title = item.get("title", "농심 신라면 120g 20개")
    price = item.get("price", 0)
    mall = item.get("mall", "네이버 가격비교 카탈로그")
    reviews = item.get("reviews", 0)
    item_url = item.get("url", "https://shopping.naver.com")

    deliv_fee = item.get("delivery_fee", 0)
    deliv_text = " (무료배송)" if deliv_fee == 0 else f" (+배송비 {deliv_fee:,}원)"

    # 메시지 본문 구성
    text_content = (
        f"🚨 [특가 발생] 목표 가격 이하입니다!\n\n"
        f"• 상품명: {title}\n"
        f"• 가격: {price:,}원{deliv_text}\n"
        f"• 쇼핑몰: {mall}\n"
        f"• 리뷰 수: {reviews:,}개"
    )

    template_object = {
        "object_type": "text",
        "text": text_content,
        "link": {
            "web_url": item_url,
            "mobile_web_url": item_url
        },
        "button_title": "구매하러 가기"
    }

    payload = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }
    encoded_payload = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(send_url, data=encoded_payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("result_code") == 0:
                return True, "카카오톡 메시지 전송 성공"
            else:
                return False, f"카카오톡 API 에러 응답: {res}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return False, f"카카오톡 전송 HTTP {e.code} 에러: {err_body}"
    except Exception as e:
        return False, f"카카오톡 전송 실패: {e}"


# =====================================================================
# 네이버 쇼핑 수집 및 필터링 관련 함수
# =====================================================================

def fetch_search_html(query: str) -> str:
    """네이버 통합검색에서 검색 결과 HTML을 가져옵니다."""
    encoded_query = urllib.parse.quote(query)
    url = f"https://search.naver.com/search.naver?where=nexearch&query={encoded_query}"
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status != 200:
                raise RuntimeError(f"네이버 서버 응답 오류 (HTTP {resp.status})")
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"네이버 검색 요청 실패: {e}")


def extract_paged_slots_from_html(html: str) -> List[Dict[str, Any]]:
    """HTML 내부의 pagedSlot JSON 데이터를 추출합니다."""
    pattern = r'<script[^>]*>([\s\S]*?)</script>'
    for script in re.findall(pattern, html, flags=re.IGNORECASE):
        if "pagedSlot" in script and "productName" in script:
            idx = script.find('{"initProps"')
            if idx != -1:
                sub = script[idx:]
                newline_idx = sub.find('\n')
                json_str = sub[:newline_idx].strip() if newline_idx != -1 else sub.strip()
                try:
                    data = json.loads(json_str)
                    paged_slots = data.get("initProps", {}).get("pagedSlot", [])
                    if paged_slots and "slots" in paged_slots[0]:
                        return paged_slots[0]["slots"]
                except Exception:
                    continue
    raise ValueError("HTML에서 쇼핑 상품 데이터(pagedSlot JSON)를 찾을 수 없습니다.")


def parse_and_filter_slots(
    slots: List[Dict[str, Any]],
    exclude_keywords: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """슬롯 데이터에서 광고 및 변형 상품을 제거하고 순수 신라면 20개 상품만 정제 및 정렬합니다."""
    if exclude_keywords is None:
        exclude_keywords = EXCLUDE_KEYWORDS

    cleaned_items: List[Dict[str, Any]] = []
    seen_ids = set()

    for slot in slots:
        d = slot.get("data", slot)
        if not isinstance(d, dict):
            continue

        # 3. 광고 상품 필터링 (광고 제거)
        is_ad = (
            d.get("sourceType") == "AD"
            or d.get("cardType") == "AD_CARD"
            or bool(d.get("isAd"))
            or bool(d.get("is_ad"))
        )
        if is_ad:
            continue

        raw_title = d.get("productName") or d.get("title") or ""
        title = re.sub(r'<[^>]+>', '', str(raw_title)).strip()
        if not title:
            continue

        # 변형 상품 및 믹스 라면 제외
        if any(kw in title for kw in exclude_keywords):
            continue

        # 20개입 확인
        if "20" not in title:
            continue

        # 가격 확인
        price = d.get("discountedSalePrice") or d.get("salePrice") or d.get("price") or 0
        try:
            price = int(price)
        except (ValueError, TypeError):
            continue
        if price <= 0:
            continue

        # 배송비
        delivery_info = d.get("productDeliveryInfo", {})
        delivery_fee = 0
        if isinstance(delivery_info, dict) and "deliveryFee" in delivery_info:
            try:
                delivery_fee = int(delivery_info["deliveryFee"])
            except (ValueError, TypeError):
                delivery_fee = 0
        elif "deliveryFee" in d:
            try:
                delivery_fee = int(d.get("deliveryFee") or 0)
            except (ValueError, TypeError):
                delivery_fee = 0

        # 쇼핑몰명
        mall = d.get("mallName") or d.get("mall") or "네이버 가격비교 카탈로그"

        # 링크
        url = ""
        click_url = d.get("productClickUrl")
        if isinstance(click_url, dict):
            url = click_url.get("pcUrl") or click_url.get("mobileUrl") or ""
        if not url:
            prod_url = d.get("productUrl")
            if isinstance(prod_url, dict):
                url = prod_url.get("pcUrl") or prod_url.get("mobileUrl") or ""
            elif isinstance(d.get("url"), str):
                url = d["url"]

        # 리뷰 수
        reviews = d.get("totalReviewCount") or d.get("reviews") or 0
        try:
            reviews = int(reviews)
        except (ValueError, TypeError):
            reviews = 0

        # 중복 방지
        item_id = str(d.get("nvMid") or d.get("productId") or title)
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        cleaned_items.append({
            "title": title,
            "price": price,
            "delivery_fee": delivery_fee,
            "total_price": price + delivery_fee,
            "mall": str(mall).strip(),
            "reviews": reviews,
            "url": str(url).strip()
        })

    # 최저가 순서로 정렬
    cleaned_items.sort(key=lambda x: x["price"])
    return cleaned_items


def get_target_price_message(lowest_price: int, target_price: int = TARGET_PRICE) -> str:
    """1위 가격과 목표가를 비교하여 결과 메시지를 반환합니다."""
    if lowest_price <= target_price:
        return SPECIAL_PRICE_MSG
    else:
        return MAINTAIN_PRICE_MSG


def format_results(items: List[Dict[str, Any]], target_price: int = TARGET_PRICE) -> str:
    """상위 3개 상품과 특가/유지 판정 결과를 서식화하여 반환합니다."""
    lines = []
    top3 = items[:3]
    lines.append(f"📦 [농심 신라면 20개입 최저가 TOP {len(top3)}]\n")

    for idx, item in enumerate(top3, start=1):
        deliv = "무료배송" if item['delivery_fee'] == 0 else f"+배송비 {item['delivery_fee']:,}원"
        lines.append(f"[{idx}위] {item['title']} (쇼핑몰: {item['mall']})")
        lines.append(f"   💰 가격    : {item['price']:,}원 ({deliv} / 총 {item['total_price']:,}원)")
        lines.append(f"   ⭐ 리뷰 수 : {item['reviews']:,}개")
        lines.append(f"   🔗 링크    : {item['url']}")
        lines.append("-" * 60)

    msg = get_target_price_message(top3[0]["price"], target_price)
    lines.append("")
    lines.append(msg)
    return "\n".join(lines)


def run_bot(
    data_source: Any = None,
    target_price: int = TARGET_PRICE,
    send_notification: bool = False
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """
    봇의 메인 비즈니스 로직.
    - data_source: None이면 실제 네이버 요청, 문자열이면 HTML 본문, 리스트/딕셔너리면 가짜 슬롯/상품 데이터
    - send_notification: True일 때 1위 상품이 목표가 이하이면 카카오톡 알림 전송
    - 반환값: (성공 여부: bool, 출력 메시지 또는 오류 메시지: str, 정제된 상품 목록: list)
    """
    try:
        # 데이터 수집
        if data_source is None:
            slots = []
            queries = [DEFAULT_QUERY, "농심 신라면 120g 20개", "신라면 20개"]
            errors = []
            for q in queries:
                try:
                    html = fetch_search_html(q)
                    slots.extend(extract_paged_slots_from_html(html))
                    filtered = parse_and_filter_slots(slots)
                    if len(filtered) >= 3:
                        break
                except Exception as e:
                    errors.append(f"[{q}] {e}")

            if not slots:
                err_detail = "; ".join(errors) if errors else "데이터를 전혀 수집하지 못했습니다."
                raise RuntimeError(f"네이버 쇼핑 상품을 조회할 수 없습니다. 상세: {err_detail}")

        elif isinstance(data_source, str):
            slots = extract_paged_slots_from_html(data_source)
        elif isinstance(data_source, list):
            slots = data_source
        elif isinstance(data_source, dict):
            if "slots" in data_source:
                slots = data_source["slots"]
            elif "initProps" in data_source:
                slots = data_source["initProps"]["pagedSlot"][0]["slots"]
            else:
                slots = [data_source]
        else:
            raise ValueError(f"지원하지 않는 데이터 소스 형식: {type(data_source)}")

        # 데이터 필터링 및 정렬
        items = parse_and_filter_slots(slots)

        if not items:
            raise RuntimeError("검색 결과에 조건에 맞는 유효한 신라면 20개 상품이 없습니다.")

        output_text = format_results(items, target_price=target_price)

        # 1. 1위 가격이 목표가 이하일 때만 카카오톡 알림 전송 (비싸면 화면에만 출력하고 전송 안 함)
        top_item = items[0]
        if top_item["price"] <= target_price:
            if send_notification:
                memo_ok, memo_msg = send_kakao_memo(top_item)
                if memo_ok:
                    output_text += "\n\n📲 [카카오톡 알림] 1위 특가 상품 정보가 카카오톡 '나에게 보내기'로 전송되었습니다!"
                else:
                    output_text += f"\n\n❌ [카카오톡 알림 전송 실패] 알림을 보내지 못했습니다: {memo_msg}"
        else:
            if send_notification:
                output_text += f"\n\nℹ️ 1위 가격({top_item['price']:,}원)이 목표가({target_price:,}원)보다 비싸므로 카카오톡 알림을 전송하지 않았습니다."

        return True, output_text, items

    except Exception as err:
        err_msg = f"❌ [오류 발생] 라면 최저가 정보를 가져오지 못했습니다: {err}"
        return False, err_msg, []


def main():
    print("=" * 60)
    print("  🍜 [percent_bot] 라면 최저가 탐색 봇 시작")
    print("=" * 60)
    print(f"• 검색 대상: '{DEFAULT_QUERY}'")
    print(f"• 목표 가격: {TARGET_PRICE:,}원\n")

    # 실제 실행 시 카카오톡 알림 전송 활성화
    success, message, _ = run_bot(send_notification=True)
    print(message)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
