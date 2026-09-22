"""팀 이름 한국어 표기.

ESPN·MLB 공식 기록은 영어 이름이라 화면에만 한국어로 바꿔 보여준다.
사전에 없는 팀은 영어 그대로 둔다. (대표팀 Elo 비교 등 안쪽 계산은 영어 이름을 그대로 쓴다)
"""

from __future__ import annotations

import re
import unicodedata

MLB = {
    "Arizona Diamondbacks": "애리조나", "Atlanta Braves": "애틀랜타", "Baltimore Orioles": "볼티모어",
    "Boston Red Sox": "보스턴", "Chicago Cubs": "시카고 컵스", "Chicago White Sox": "시카고 화이트삭스",
    "Cincinnati Reds": "신시내티", "Cleveland Guardians": "클리블랜드", "Colorado Rockies": "콜로라도",
    "Detroit Tigers": "디트로이트", "Houston Astros": "휴스턴", "Kansas City Royals": "캔자스시티",
    "Los Angeles Angels": "LA 에인절스", "Los Angeles Dodgers": "LA 다저스", "Miami Marlins": "마이애미",
    "Milwaukee Brewers": "밀워키", "Minnesota Twins": "미네소타", "New York Mets": "뉴욕 메츠",
    "New York Yankees": "뉴욕 양키스", "Athletics": "애슬레틱스", "Oakland Athletics": "애슬레틱스",
    "Philadelphia Phillies": "필라델피아", "Pittsburgh Pirates": "피츠버그", "San Diego Padres": "샌디에이고",
    "San Francisco Giants": "샌프란시스코", "Seattle Mariners": "시애틀", "St. Louis Cardinals": "세인트루이스",
    "Tampa Bay Rays": "탬파베이", "Texas Rangers": "텍사스", "Toronto Blue Jays": "토론토",
    "Washington Nationals": "워싱턴",
}

CLUBS = {
    # 잉글랜드
    "Arsenal": "아스널", "Aston Villa": "애스턴 빌라", "Bournemouth": "본머스", "AFC Bournemouth": "본머스",
    "Brentford": "브렌트퍼드", "Brighton & Hove Albion": "브라이턴", "Brighton": "브라이턴", "Burnley": "번리",
    "Chelsea": "첼시", "Crystal Palace": "크리스털 팰리스", "Everton": "에버턴", "Fulham": "풀럼",
    "Leeds United": "리즈", "Leicester City": "레스터", "Liverpool": "리버풀", "Manchester City": "맨시티",
    "Manchester United": "맨유", "Newcastle United": "뉴캐슬", "Nottingham Forest": "노팅엄",
    "Southampton": "사우샘프턴", "Sunderland": "선덜랜드", "Tottenham Hotspur": "토트넘", "West Ham United": "웨스트햄",
    "Wolverhampton Wanderers": "울버햄튼", "Ipswich Town": "입스위치", "Sheffield United": "셰필드 유나이티드",
    "Norwich City": "노리치", "Watford": "왓퍼드", "Middlesbrough": "미들즈브러", "Coventry City": "코번트리",
    "Hull City": "헐 시티", "West Bromwich Albion": "웨스트브롬", "Stoke City": "스토크", "Luton Town": "루턴",
    # 스페인
    "Real Madrid": "레알 마드리드", "Barcelona": "바르셀로나", "Atletico Madrid": "아틀레티코 마드리드",
    "Atlético Madrid": "아틀레티코 마드리드", "Athletic Club": "아틀레틱 빌바오", "Real Sociedad": "레알 소시에다드",
    "Real Betis": "레알 베티스", "Villarreal": "비야레알", "Valencia": "발렌시아", "Sevilla": "세비야",
    "Getafe": "헤타페", "Girona": "지로나", "Celta Vigo": "셀타 비고", "Osasuna": "오사수나",
    "Rayo Vallecano": "라요 바예카노", "Mallorca": "마요르카", "Alaves": "알라베스", "Deportivo Alaves": "알라베스",
    "Espanyol": "에스파뇰", "Las Palmas": "라스팔마스", "Leganes": "레가네스", "Real Valladolid": "바야돌리드",
    "Levante": "레반테", "Elche": "엘체", "Real Oviedo": "오비에도", "Racing Santander": "라싱",
    # 이탈리아
    "Internazionale": "인테르", "Inter Milan": "인테르", "AC Milan": "AC 밀란", "Juventus": "유벤투스",
    "Napoli": "나폴리", "AS Roma": "AS 로마", "Roma": "AS 로마", "Lazio": "라치오", "Atalanta": "아탈란타",
    "Fiorentina": "피오렌티나", "Bologna": "볼로냐", "Torino": "토리노", "Genoa": "제노아", "Udinese": "우디네세",
    "Lecce": "레체", "Cagliari": "칼리아리", "Parma": "파르마", "Como": "코모", "Empoli": "엠폴리",
    "Hellas Verona": "베로나", "Verona": "베로나", "Monza": "몬차", "Venezia": "베네치아", "Sassuolo": "사수올로",
    "Pisa": "피사", "Cremonese": "크레모네세", "Frosinone": "프로시노네", "Salernitana": "살레르니타나",
    # 독일
    "Bayern Munich": "바이에른 뮌헨", "Borussia Dortmund": "도르트문트", "Bayer Leverkusen": "레버쿠젠",
    "RB Leipzig": "라이프치히", "Eintracht Frankfurt": "프랑크푸르트", "VfB Stuttgart": "슈투트가르트",
    "SC Freiburg": "프라이부르크", "VfL Wolfsburg": "볼프스부르크", "Borussia Monchengladbach": "묀헨글라트바흐",
    "Union Berlin": "우니온 베를린", "1. FC Union Berlin": "우니온 베를린", "Werder Bremen": "베르더 브레멘",
    "TSG Hoffenheim": "호펜하임", "Hoffenheim": "호펜하임", "FC Augsburg": "아우크스부르크", "Augsburg": "아우크스부르크",
    "Mainz": "마인츠", "Mainz 05": "마인츠", "1. FC Heidenheim 1846": "하이덴하임", "Heidenheim": "하이덴하임",
    "St. Pauli": "장크트파울리", "FC St. Pauli": "장크트파울리", "Holstein Kiel": "홀슈타인 킬", "VfL Bochum": "보훔",
    "1. FC Koln": "쾰른", "FC Cologne": "쾰른", "Hamburg SV": "함부르크", "Hamburger SV": "함부르크",
    "Schalke 04": "샬케", "Hertha Berlin": "헤르타", "SC Paderborn 07": "파더보른", "Paderborn": "파더보른",
    # 프랑스
    "Paris Saint-Germain": "파리 생제르맹", "Marseille": "마르세유", "AS Monaco": "모나코", "Monaco": "모나코",
    "Lille": "릴", "Lyon": "리옹", "Nice": "니스", "Lens": "랑스", "Rennes": "렌", "Stade Rennais": "렌",
    "Strasbourg": "스트라스부르", "Nantes": "낭트", "Toulouse": "툴루즈", "Brest": "브레스트", "Stade Brestois": "브레스트",
    "Reims": "랭스", "Montpellier": "몽펠리에", "Auxerre": "오세르", "AJ Auxerre": "오세르", "Angers": "앙제",
    "Le Havre": "르아브르", "Le Havre AC": "르아브르", "Saint-Etienne": "생테티엔", "Lorient": "로리앙", "Metz": "메스",
    "Paris FC": "파리 FC",
    # 유럽 대회 단골
    "Benfica": "벤피카", "FC Porto": "포르투", "Porto": "포르투", "Sporting CP": "스포르팅", "Sporting Lisbon": "스포르팅",
    "Ajax": "아약스", "Ajax Amsterdam": "아약스", "PSV Eindhoven": "PSV", "PSV": "PSV", "Feyenoord": "페예노르트",
    "Celtic": "셀틱", "Rangers": "레인저스", "Galatasaray": "갈라타사라이", "Fenerbahce": "페네르바체",
    "Besiktas": "베식타스", "Club Brugge": "클럽 브뤼헤", "Red Bull Salzburg": "잘츠부르크", "Shakhtar Donetsk": "샤흐타르",
    "Olympiacos": "올림피아코스", "FC Copenhagen": "코펜하겐", "Slavia Prague": "슬라비아 프라하",
    "Sparta Prague": "스파르타 프라하", "Dinamo Zagreb": "디나모 자그레브", "Young Boys": "영 보이스",
    # J리그
    "Vissel Kobe": "비셀 고베", "Gamba Osaka": "감바 오사카", "Kashima Antlers": "가시마 앤틀러스",
    "Urawa Red Diamonds": "우라와 레즈", "Kawasaki Frontale": "가와사키 프론탈레", "Yokohama F. Marinos": "요코하마 F. 마리노스",
    "FC Tokyo": "FC 도쿄", "Sanfrecce Hiroshima": "산프레체 히로시마", "Nagoya Grampus": "나고야 그램퍼스",
    "Cerezo Osaka": "세레소 오사카", "Kashiwa Reysol": "가시와 레이솔", "Machida Zelvia": "마치다 젤비아",
    "FC Machida Zelvia": "마치다 젤비아", "Tokyo Verdy": "도쿄 베르디", "Albirex Niigata": "알비렉스 니가타",
    "Shonan Bellmare": "쇼난 벨마레", "Avispa Fukuoka": "아비스파 후쿠오카", "Hokkaido Consadole Sapporo": "콘사도레 삿포로",
    "Kyoto Sanga": "교토 상가", "Jubilo Iwata": "주빌로 이와타", "Sagan Tosu": "사간 도스", "Shimizu S-Pulse": "시미즈 S펄스",
    "Fagiano Okayama": "파지아노 오카야마", "Yokohama FC": "요코하마 FC",
    "Kyoto Sanga FC": "교토 상가", "Consadole Sapporo": "콘사도레 삿포로", "Urawa Reds": "우라와 레즈",
    # J2·J3 (일왕배에 자주 나옴)
    "Tokushima Vortis": "도쿠시마 보르티스", "Ventforet Kofu": "반포레 고후", "FC Imabari": "FC 이마바리",
    "Fujieda MYFC": "후지에다 MYFC", "Tochigi City FC": "도치기 시티", "Tochigi City": "도치기 시티", "Tochigi SC": "도치기 SC",
    "V-Varen Nagasaki": "V바렌 나가사키", "JEF United Chiba": "JEF 유나이티드 지바", "JEF United": "JEF 유나이티드 지바",
    "Vegalta Sendai": "베갈타 센다이", "Montedio Yamagata": "몬테디오 야마가타", "Oita Trinita": "오이타 트리니타",
    "Roasso Kumamoto": "로아소 구마모토", "Blaublitz Akita": "블라우블리츠 아키타", "Iwaki FC": "이와키 FC",
    "Mito HollyHock": "미토 홀리호크", "Mito Hollyhock": "미토 홀리호크", "Renofa Yamaguchi": "레노파 야마구치",
    "Ehime FC": "에히메 FC", "Kataller Toyama": "카탈레 도야마", "Zweigen Kanazawa": "츠바이겐 가나자와",
    "Thespa Gunma": "테스파 군마", "Thespakusatsu Gunma": "테스파 군마", "Kagoshima United": "가고시마 유나이티드",
    "Omiya Ardija": "오미야 아르디자", "RB Omiya Ardija": "RB 오미야 아르디자", "Giravanz Kitakyushu": "기라반츠 기타큐슈",
    "Tokushima": "도쿠시마 보르티스", "FC Gifu": "FC 기후", "Matsumoto Yamaga": "마쓰모토 야마가", "Nagano Parceiro": "나가노 파르세이로",
    "Kamatamare Sanuki": "가마타마레 사누키", "FC Ryukyu": "FC 류큐", "Azul Claro Numazu": "아술 클라로 누마즈",
    "Gainare Tottori": "가이나레 돗토리", "SC Sagamihara": "SC 사가미하라", "Iwate Grulla Morioka": "이와테 그루자 모리오카",
    "Vanraure Hachinohe": "반라우레 하치노헤", "FC Osaka": "FC 오사카", "Nara Club": "나라 클럽", "Kochi United": "고치 유나이티드",
}

NATIONS = {
    # 아시아·오세아니아
    "South Korea": "대한민국", "Korea Republic": "대한민국", "Japan": "일본", "China": "중국", "China PR": "중국",
    "Australia": "호주", "Iran": "이란", "IR Iran": "이란", "Saudi Arabia": "사우디아라비아", "Qatar": "카타르",
    "Iraq": "이라크", "United Arab Emirates": "아랍에미리트", "UAE": "아랍에미리트", "Uzbekistan": "우즈베키스탄",
    "Jordan": "요르단", "Oman": "오만", "Bahrain": "바레인", "Kuwait": "쿠웨이트", "Syria": "시리아", "Lebanon": "레바논",
    "Palestine": "팔레스타인", "Thailand": "태국", "Vietnam": "베트남", "Indonesia": "인도네시아", "Malaysia": "말레이시아",
    "Philippines": "필리핀", "Singapore": "싱가포르", "India": "인도", "Kyrgyzstan": "키르기스스탄", "Tajikistan": "타지키스탄",
    "North Korea": "북한", "Korea DPR": "북한", "Hong Kong": "홍콩", "Chinese Taipei": "대만", "New Zealand": "뉴질랜드",
    "Fiji": "피지", "Vanuatu": "바누아투", "New Caledonia": "뉴칼레도니아", "Solomon Islands": "솔로몬 제도",
    "Papua New Guinea": "파푸아뉴기니", "Tahiti": "타히티", "Turkmenistan": "투르크메니스탄", "Myanmar": "미얀마",
    # 유럽
    "England": "잉글랜드", "France": "프랑스", "Germany": "독일", "Spain": "스페인", "Italy": "이탈리아",
    "Portugal": "포르투갈", "Netherlands": "네덜란드", "Belgium": "벨기에", "Croatia": "크로아티아",
    "Switzerland": "스위스", "Denmark": "덴마크", "Sweden": "스웨덴", "Norway": "노르웨이", "Poland": "폴란드",
    "Austria": "오스트리아", "Czechia": "체코", "Czech Republic": "체코", "Serbia": "세르비아", "Scotland": "스코틀랜드",
    "Wales": "웨일스", "Republic of Ireland": "아일랜드", "Ireland": "아일랜드", "Northern Ireland": "북아일랜드",
    "Ukraine": "우크라이나", "Turkey": "튀르키예", "Turkiye": "튀르키예", "Greece": "그리스", "Hungary": "헝가리",
    "Romania": "루마니아", "Slovakia": "슬로바키아", "Slovenia": "슬로베니아", "Finland": "핀란드", "Iceland": "아이슬란드",
    "Albania": "알바니아", "Georgia": "조지아", "Bosnia and Herzegovina": "보스니아 헤르체고비나",
    "Bosnia-Herzegovina": "보스니아 헤르체고비나", "North Macedonia": "북마케도니아", "Montenegro": "몬테네그로",
    "Kosovo": "코소보", "Israel": "이스라엘", "Bulgaria": "불가리아", "Belarus": "벨라루스", "Lithuania": "리투아니아",
    "Latvia": "라트비아", "Estonia": "에스토니아", "Azerbaijan": "아제르바이잔", "Armenia": "아르메니아",
    "Kazakhstan": "카자흐스탄", "Luxembourg": "룩셈부르크", "Cyprus": "키프로스", "Malta": "몰타", "Moldova": "몰도바",
    "Faroe Islands": "페로 제도", "Andorra": "안도라", "San Marino": "산마리노", "Liechtenstein": "리히텐슈타인",
    "Gibraltar": "지브롤터", "Russia": "러시아",
    # 아메리카
    "Argentina": "아르헨티나", "Brazil": "브라질", "Uruguay": "우루과이", "Colombia": "콜롬비아", "Ecuador": "에콰도르",
    "Chile": "칠레", "Peru": "페루", "Paraguay": "파라과이", "Venezuela": "베네수엘라", "Bolivia": "볼리비아",
    "United States": "미국", "USA": "미국", "Mexico": "멕시코", "Canada": "캐나다", "Costa Rica": "코스타리카",
    "Panama": "파나마", "Jamaica": "자메이카", "Honduras": "온두라스", "El Salvador": "엘살바도르", "Guatemala": "과테말라",
    "Haiti": "아이티", "Trinidad and Tobago": "트리니다드 토바고", "Curacao": "퀴라소", "Dominica": "도미니카 연방",
    "Dominican Republic": "도미니카 공화국", "Anguilla": "앵귈라", "Cuba": "쿠바", "Nicaragua": "니카라과",
    "Suriname": "수리남", "Guyana": "가이아나", "Bermuda": "버뮤다", "Puerto Rico": "푸에르토리코",
    # 아프리카
    "Morocco": "모로코", "Senegal": "세네갈", "Egypt": "이집트", "Nigeria": "나이지리아", "Ghana": "가나",
    "Cameroon": "카메룬", "Ivory Coast": "코트디부아르", "Cote d'Ivoire": "코트디부아르", "Algeria": "알제리",
    "Tunisia": "튀니지", "South Africa": "남아프리카 공화국", "Mali": "말리", "Burkina Faso": "부르키나파소",
    "DR Congo": "콩고민주공화국", "Congo DR": "콩고민주공화국", "Cape Verde": "카보베르데", "Cabo Verde": "카보베르데",
    "Guinea": "기니", "Zambia": "잠비아", "Angola": "앙골라", "Gabon": "가봉", "Uganda": "우간다", "Kenya": "케냐",
    "Benin": "베냉", "Libya": "리비아", "Equatorial Guinea": "적도기니", "Mozambique": "모잠비크", "Tanzania": "탄자니아",
    "Zimbabwe": "짐바브웨", "Sudan": "수단", "Namibia": "나미비아", "Madagascar": "마다가스카르",
}


def _key(name):
    t = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    t = t.replace("&", " and ").replace("'", "")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_TABLE = {}
for _src in (MLB, CLUBS, NATIONS):
    for _en, _ko in _src.items():
        _TABLE.setdefault(_key(_en), _ko)

_WOMEN = re.compile(r"\s*(women|womens|w)$")


def ko_team(name):
    """영어 팀 이름 -> 한국어 (사전에 없으면 원래 이름)."""
    raw = str(name or "")
    if not raw or re.search(r"[가-힣]", raw):
        return raw                                   # 이미 한국어(네이버 등)
    k = _key(raw)
    if k in _TABLE:
        return _TABLE[k]
    m = _WOMEN.search(k)
    if m:                                            # 여자 대표팀: "South Korea Women" -> "대한민국 여자"
        base = k[:m.start()].strip()
        if base in _TABLE:
            return _TABLE[base] + " 여자"
    return raw
