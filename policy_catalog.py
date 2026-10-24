from __future__ import annotations

from copy import deepcopy
from datetime import date

from policy_core import PolicyValidationError


PROFILE_BOOLEAN_FIELDS = frozenset({
    "tianjin_college", "tianjin_origin", "support_condition", "within_two_years",
    "first_employment", "registered_flexible", "paid_social", "ordinary_degree",
    "seeking_work", "grant_not_received", "nonlocal_registration", "has_identity",
    "has_registration", "residence_half_year", "stable_basis", "beijing_hebei_permit",
    "has_photo", "has_address_proof",
})
PROFILE_STAGES = frozenset({"student", "graduate", "employed", "flexible", "other", "unknown"})
PUBLIC_JOB_ENTRY = "https://job.hrss.tj.gov.cn/"
CHECKED_AT = "2026-10-24"


def _condition(key: str, label: str, **extra: object) -> dict:
    return {"id": key, "fact": key, "label": label, "expected": True, **extra}


def _material(key: str, label: str, note: str = "") -> dict:
    return {"id": key, "label": label, "note": note}


_POLICIES = (
    {
        "id": "job-seeking-grant", "title": "2027届高校毕业生一次性求职补贴",
        "category": "求职支持", "benefit": "3000元 / 人",
        "summary": "面向天津高校毕业学年中符合规定条件、有就业创业意愿的2027届毕业生，一次申领。",
        "issuer": "天津市人力资源和社会保障局、天津市教育委员会、天津市财政局",
        "document_number": "津人社办发〔2026〕37号",
        "published_at": "2026-10-08", "effective_at": None,
        "deadline": "2026-10-31", "valid_until": None,
        "source_url": "https://www.tj.gov.cn/zwgk/zfxxgkzl/fdzdgknr/zdmsxx220316/jy_157359/zcwj_157391/202610/t20261009_7390207.html",
        "source_checked_at": CHECKED_AT,
        "reviewed_source_fingerprint": "cfb54389d437c13669757b5137a4bd36cdf5f421383bd58abc3e2554e48022c6",
        "scope": "2027届本市普通高校毕业学年学生；含通知列明的技工、特殊教育院校毕业生。",
        "conditions": [
            _condition("graduation_year", "属于2027届毕业学年毕业生", expected_value=2027),
            _condition("tianjin_college", "就读于天津市通知覆盖的院校"),
            _condition("seeking_work", "有就业创业意愿且正在积极求职创业"),
            _condition("grant_not_received", "尚未领取过这项一次性求职补贴"),
            _condition("support_condition", "符合通知列明的五类支持条件之一"),
        ],
        "materials": [
            _material("application", "天津市一次性求职补贴申请表", "使用官方通知附件1。"),
            _material("identity", "本人身份证复印件", "请提交给官方办理渠道，本工具不接收证件。"),
            _material("social_card", "本人社会保障卡复印件", "需激活金融服务功能。"),
            _material("support_proof", "所选支持类别对应的证明", "例如国家助学贷款合同及贷款发放证明；其他类别见官方原文。"),
            _material("school_requirements", "所在院校补充要求", "先向学校确认具体截止时间和材料格式。"),
        ],
        "steps": ["打开天津公共就业服务网，核对学校通知。", "准备申请表和对应证明，在学校要求的时间内提交申请。",
                  "关注学校审核、公示和补正通知。", "审核、公示通过后，补贴拨付至本人社保卡银行账户。"],
        "cautions": ["不是所有毕业生都适用；须同时满足院校、届次、意愿及支持类别等要求。",
                     "五类条件：低保家庭、零就业家庭、残疾、获得国家助学贷款、特困人员中的高校毕业生。",
                     "每人限领一次，多种支持条件不能重复申领。", "10月31日为通知申请节点，院校可制定具体要求，请先核对校内安排。",
                     "仅按你提供的信息做初步核对，最终以学校和人社部门审核为准。"],
        "queries": ["天津 2027届 高校毕业生 一次性求职补贴 津人社办发 2026 37号",
                    "天津 2027届 一次性求职补贴 申请 补充通知 截止"],
        "official_entry": PUBLIC_JOB_ENTRY,
        "applies_to": ["天津市通知覆盖院校的2027届毕业学年学生，符合规定支持条件"],
        "what_changed": [],
        "actions": ["核对学校申请安排", "确认是否符合支持条件", "准备申请表、证件复印件和对应证明"],
        "body": "2027届本市普通高校有就业创业意愿、积极求职创业且符合下列任一条件的毕业学年高校毕业生。"
                "一次性求职补贴标准为每人3000元，每人限领一次。符合条件的高校毕业生，10月31日前，"
                "登录天津公共就业服务网，向所在院校提出补贴申请。各院校可结合实际制定补贴申请的具体要求。",
    },
    {
        "id": "flexible-employment", "title": "高校毕业生灵活就业社保补贴",
        "category": "就业保障", "benefit": "最高800元 / 月",
        "summary": "符合条件的高校毕业生灵活就业并缴纳社保后，可按规定申请养老、医疗保险补贴，最长2年。",
        "issuer": "天津市人力资源和社会保障局、天津市财政局",
        "document_number": "津人社办发〔2024〕50号",
        "published_at": "2024-12-24", "effective_at": "2025-01-01",
        "deadline": None, "valid_until": "2027-12-31", "recurring_deadline_day": 20,
        "source_url": "https://hrss.tj.gov.cn/zhengwugongkai/zhengcezhinan/zxwjnew/202412/t20241224_6813668.html",
        "source_checked_at": CHECKED_AT,
        "reviewed_source_fingerprint": "dab3272fa4cbf99f4059927c01953028eb312aeb4045f733c3e64a378cd38693",
        "scope": "本市院校毕业生，或本市生源外省市院校毕业生；首次申请须在毕业证发证24个月内。",
        "conditions": [
            _condition("ordinary_degree", "已取得境内普通高校毕业证书"),
            _condition("tianjin_college", "属于本市院校毕业生或本市生源", any_of=["tianjin_college", "tianjin_origin"]),
            _condition("within_two_years", "首次申请距离毕业证发证日期不超过24个月"),
            _condition("first_employment", "离校后未在单位就业，也未任企业法人或有雇工的个体经营者"),
            _condition("registered_flexible", "已在天津办理灵活就业登记"),
            _condition("paid_social", "已依法缴纳企业职工养老或职工基本医疗保险费"),
        ],
        "materials": [
            _material("application", "高校毕业生灵活就业社会保险补贴申请表", "使用官方通知附件。"),
            _material("degree_report", "教育部学籍在线验证报告", "原文列明仅首月申请提供，具体格式向经办机构确认。"),
            _material("origin_proof", "户口本首页及本人页复印件", "仅本市生源外省市院校毕业生首月申请时提供。"),
            _material("payment_proof", "社会保险费缴费凭证", "按实际申领月份和已缴险种准备。"),
        ],
        "steps": ["核对院校、生源、毕业时间和就业情况，完成灵活就业登记。",
                  "按规定缴纳社保，保存缴费凭证。", "按通知每月20日前，向居住地区人社局或天津公共就业服务网申请。",
                  "关注审核、公示和拨付结果；就业状态变化时及时联系经办机构。"],
        "cautions": ["养老保险补贴600元/月，医疗保险补贴200元/月，两项同时缴纳时800元/月。",
                     "先缴后补，首次申请当月起算，最长2年；未缴费的月份和险种不补贴。",
                     "后续补贴可按月、季、半年申领或当年年底前集中申领；每月20日是办理节点，不是政策失效日。",
                     "单位就业、担任企业法人或有雇工个体经营者等情况将终止补贴。",
                     "政策有效期至2027年12月31日，具体金额和可补月份由经办机构核定。"],
        "queries": ["天津 高校毕业生 灵活就业 社会保险补贴 津人社办发 2024 50号",
                    "天津 高校毕业生 灵活就业 社保补贴 调整 废止 最新"],
        "official_entry": PUBLIC_JOB_ENTRY,
        "applies_to": ["本市院校或本市生源外省市院校离校2年内未就业的高校毕业生"],
        "what_changed": [],
        "actions": ["完成灵活就业登记", "先缴纳社保再申请补贴", "核对毕业24个月内首次申请期限"],
        "body": "通过无雇工个体经营、非全日制以及新就业形态等方式实现灵活就业，在本市办理灵活就业登记，"
                "依法缴纳社会保险费的本市院校和本市生源外省市院校离校2年内未就业的高校毕业生。"
                "缴纳企业职工基本养老保险费，每人每月补贴600元；缴纳职工基本医疗保险费，每人每月补贴200元。"
                "同时缴纳的，每人每月补贴800元。补贴时间自首次申请补贴当月起计算，最长2年。"
                "本通知自2025年1月1日起施行，有效期至2027年12月31日。",
    },
    {
        "id": "residence-permit", "title": "天津居住证首次申领",
        "category": "在津生活", "benefit": "首次申领免费",
        "summary": "来津居住的非本市户籍人员，符合居住期限和稳定住所、就业或就读条件后，可按规定申领。",
        "issuer": "天津市人民政府", "document_number": "津政规〔2021〕6号",
        "published_at": "2021-12-31", "effective_at": "2022-01-01",
        "deadline": None, "valid_until": "2026-12-31",
        "source_url": "https://www.tj.gov.cn/zwgk/szfwj/tjsrmzf/202112/t20211231_5768456.html",
        "source_checked_at": CHECKED_AT,
        "reviewed_source_fingerprint": "558987d2a356b136bdf99b51ba93f93a4d68613c08a38bf36a8cb9e1615dbac6",
        "scope": "离开常住户口所在地来天津居住的公民；港澳台居民居住证另按相应办法办理。",
        "conditions": [
            _condition("nonlocal_registration", "属于非天津户籍的来津居住公民"),
            _condition("stable_basis", "在天津具有合法稳定住所、合法稳定就业或连续就读条件之一"),
            _condition("residence_half_year", "已在津居住满半年，或持符合互认条件的京冀居住证",
                       any_of=["residence_half_year", "beijing_hebei_permit"]),
        ],
        "materials": [
            _material("identity", "本人居民身份证", "交给官方受理渠道，本工具不接收证件。"),
            _material("photo", "本人近期相片", "具体格式以办理渠道要求为准。"),
            _material("basis_proof", "居住地住址、就业、就读等证明材料", "按实际申请情况准备并核对受理要求。"),
            _material("mutual_permit", "北京或河北的相关居住证", "仅使用京津冀互认、免半年限制情形时提供。"),
            _material("proxy", "代办所需的合法有效证明", "仅未满16周岁、行动不便等允许代办情形适用。"),
        ],
        "steps": ["先确认居住登记和申请条件。", "通过天津网上办事大厅或居住地受理单位了解并提交材料。",
                  "关注收件凭证和补正要求。", "领取后每年签注1次，居住满1年前1个月内办理签注。"],
        "cautions": ["已居住满半年与京津冀互认例外为二选一；互认仍须符合稳定住所、就业或就读等要求。",
                     "不将居住登记、居住证申领和积分落户混为同一事项。",
                     "首次申领免费；补领、换领可能涉及工本费。",
                     "地址变动应在7日内办理变更；每年签注1次。",
                     "当前依据第47条于2026年12月31日废止，届时须查询新依据后再办理。"],
        "queries": ["天津 居住证 管理办法 津政规 2021 6号",
                    "天津 居住证 管理办法 2026 2027 修改 废止 新规定"],
        "official_entry": "https://zwfw.tj.gov.cn/",
        "applies_to": ["非天津户籍来津公民，符合居住期限和稳定住所、就业或就读等条件"],
        "what_changed": [], "actions": ["确认居住期限或京津冀互认条件", "准备身份证、相片及对应证明", "核对官方办理渠道"],
        "body": "第九条在本市居住满半年，符合有合法稳定住所、合法稳定就业、连续就读条件之一的来津公民，可以申领居住证。"
                "第十二条申领居住证，申请人应当向受理单位或者通过网络提交本人居民身份证、本人近期相片以及居住地住址、就业、就读等证明材料。"
                "第十五条持有相关省市居住证且符合条件的来津公民，不受在本市居住满半年的限制。"
                "第四十七条本办法自2022年1月1日起施行，2026年12月31日废止。",
    },
)


QUESTION_LABELS = {
    "tianjin_college": "你是否就读于或毕业于天津的相关院校？",
    "tianjin_origin": "你是否属于天津生源、在外省市院校毕业？",
    "support_condition": "你是否符合低保家庭、零就业家庭、残疾、获得国家助学贷款或特困人员中的任一支持条件？",
    "seeking_work": "你是否有就业创业意愿，且正在积极求职创业？",
    "grant_not_received": "你是否尚未领取过这项一次性求职补贴？",
    "ordinary_degree": "你是否已取得境内普通高校的研究生、本科或专科（高职）毕业证书？",
    "within_two_years": "首次申请距毕业证书发证日期是否不超过24个月（计算到月）？",
    "first_employment": "离校后你是否一直未在单位就业，也未任企业法人或有雇工的个体经营者？",
    "registered_flexible": "你是否已在天津办理灵活就业登记？",
    "paid_social": "你是否已依法缴纳职工基本养老保险费或职工基本医疗保险费？",
    "nonlocal_registration": "你是否属于非天津户籍的来津居住公民？",
    "stable_basis": "你是否在天津有合法稳定住所、合法稳定就业或连续就读条件之一？",
    "residence_half_year": "你是否已在天津居住满半年？",
    "beijing_hebei_permit": "你是否持有符合京津冀互认条件的北京或河北居住证？",
}


def validate_profile(profile: object) -> dict:
    if not isinstance(profile, dict) or not set(profile) <= PROFILE_BOOLEAN_FIELDS | {"stage", "graduation_year"}:
        raise PolicyValidationError("只接受当前问题中的情况，不接收姓名、证件号码或联系方式。")
    if any(value is not None and type(value) is not bool for key, value in profile.items() if key in PROFILE_BOOLEAN_FIELDS):
        raise PolicyValidationError("条件回答必须为是、否或不确定。")
    if "stage" in profile and (not isinstance(profile["stage"], str) or profile["stage"] not in PROFILE_STAGES):
        raise PolicyValidationError("请选择支持的当前状态。")
    year = profile.get("graduation_year")
    if year is not None and (type(year) is not int or not 1950 <= year <= 2100):
        raise PolicyValidationError("毕业年份应为有效年份或不确定。")
    return dict(profile)


def get_catalog() -> list[dict]:
    return [deepcopy(policy) for policy in _POLICIES]


def get_policy(policy_id: str) -> dict:
    for policy in _POLICIES:
        if policy["id"] == policy_id:
            return deepcopy(policy)
    raise PolicyValidationError("未找到支持的政策。")


def _condition_state(condition: dict, profile: dict) -> bool | None:
    keys = condition.get("any_of", [condition["fact"]])
    values = [profile.get(key) for key in keys]
    if "expected_value" in condition:
        value = profile.get(condition["fact"])
        return None if value is None else value == condition["expected_value"]
    return True if True in values else False if all(value is False for value in values) else None


def _questions(conditions: list[dict], profile: dict) -> list[dict]:
    keys = dict.fromkeys(key for condition in conditions for key in condition.get("any_of", [condition["fact"]])
                         if profile.get(key) is None)
    return [{"key": key, "label": "你的毕业届次是？" if key == "graduation_year" else QUESTION_LABELS[key],
             "type": "year" if key == "graduation_year" else "choice",
             "options": [{"value": year, "label": f"{year}届"} for year in (2027, 2026, 2025, 2024, 2028)]
             if key == "graduation_year" else [{"value": True, "label": "是"}, {"value": False, "label": "否"}]}
            for key in keys]


def _match(policy: dict, profile: dict, today: date) -> dict:
    until = policy.get("valid_until") or policy.get("deadline")
    if until and today > date.fromisoformat(until):
        label = "当前申请期限已过" if policy.get("deadline") else "当前政策依据已到期"
        return {"status": "expired", "reasons": [f"{label}，请查询新的官方通知。"], "questions": []}
    effective = policy.get("effective_at")
    if effective and today < date.fromisoformat(effective):
        return {"status": "unlikely", "reasons": [f"该依据自{effective}施行，当前尚未生效。"], "questions": []}
    states = [(condition, _condition_state(condition, profile)) for condition in policy["conditions"]]
    failures = [condition for condition, state in states if state is False]
    unknown = [condition for condition, state in states if state is None]
    if failures:
        return {"status": "unlikely", "reasons": [f"你提供的情况暂不满足：{item['label']}。" for item in failures], "questions": []}
    if unknown:
        reasons = [f"还需确认：{item['label']}。" for item in unknown]
        return {"status": "needs_info", "reasons": reasons, "questions": _questions(unknown, profile)}
    return {"status": "possible", "reasons": ["根据你提供的情况，已满足本工具列明的主要条件；可向官方渠道进一步核实。",
            "此结果为申请前核对，不代表学校或经办机构审核通过。"], "questions": []}


def match_policies(profile: dict, today: date | None = None) -> list[dict]:
    answers, current_day = validate_profile(profile), today or date.today()
    return [{**policy, "match": _match(policy, answers, current_day)} for policy in get_catalog()]
