# T48 — 分类置信度 + 人工复核队列

**提交** `edb89fa` · **迁移** `0026_classification_evidence.sql` · **状态** done（一个决策点待你裁定，见文末）

---

## 1. 这件事本质上不是"新功能"，是补一个 D6 缺口

`listing` 上**每一个**提取字段都带 `{conf, raw}`：

| 字段 | conf | raw |
|---|---|---|
| `salary_*` | `salary_conf` | `salary_raw` |
| `duration_months_*` | `duration_months_conf` | `duration_months_raw` |
| `study_while_work_ok_*` | `_conf` | `_raw` |
| `location_class` (0020) | `location_class_conf` | `location_class_raw` |
| `posted_at` | `posted_at_conf` | `posted_at_raw` |
| **`kind`** | **无** | **无** |
| **`position_kind`** | **无** | **无** |

唯独缺的两个，恰恰是平台自己的分类输出，而且 `kind='other'` 决定**这一行默认是否可见**。也就是说：全表最不可审计、后果最重的两个值，之前没有任何记录说明它们是**从哪段文本**得来的。D6 说"值不能脱离原始摘录存在"——这是 schema 上最后一个缺口。

顺带一提，这也是"人工复核队列"之所以此前做不了的原因：你没法复核一个看不到证据的判定。

## 2. 置信度是什么、不是什么

**它是规则强度先验，不是校准概率。** 正则分类器没有标注数据可供校准，所以 `0.95` 的含义是"这条规则指名道姓、且本仓库内未发现反例"，**不是**"95% 可能正确"。

只把三个**真实存在**的变化量折进分数：

1. **规则特异性**（每条规则的档位）——代码注释里其实已经排过序："unambiguous name-strings"（Marie Skłodowska-Curie）对"too common/ambiguous to trust standalone"（光秃秃的 `posao`）
2. **命中区域**（标题 vs 正文）
3. **反证**（是否有否定守卫先触发过）

`other` **不是** 0.0，而是 `NULL`。0.0 会断言"这肯定不是招聘"，而真实情况是"没有规则给出正面证据"。二者必须区分。

分数带 `CONFIDENCE_MODEL = "ajp-rule-prior/1"` 一并持久化，档位一改就升版本——旧分数永远能被追溯回产生它的模型。

## 3. 交付内容

**新增**
- `src/ajp/extract/evidence.py` — `Classification(kind, conf, raw, rule, zone, guards)` + 置信模型
- `src/ajp/classification_review.py` — 队列逻辑（纯函数 `doubt_reason` + DB 读写）
- `src/ajp/migrations/0026_classification_evidence.sql`
- `tools/backfill_classification_evidence.py`
- `src/ajp/api/routers/classification_review.py` — `GET /v1/classification-review`

**改造**（行为零变化，有语料测试钉死）
- `rs_text` / `intl_text` 新增 `*_with_evidence()`；公开分类器改为委托，16 个连接器全部接入
- `with_kind_override()`：连接器一旦覆盖分类器的判定（配置的 `default_kind`、board 栏目、`academic_ad`、nsz_pdf 的"招聘公告即招聘广告"兜底），**证据一并丢弃**——把一段摘录挂在一个并非由它得出的结论旁边，就是伪造出处
- `ingest.py` 接受四个新列（可选，未接入的路径留 NULL）

**测试** 1647 → **1675**（+28，全通过，0 failed / 0 errors）
`test_classification_evidence.py` 10 · `test_classification_review.py` 12 · `test_api_classification_review.py` 5 · OpenAPI 快照纯增量 +341/−0

## 4. 真实数据库上的结果（9,049 行，已执行）

```
写入证据            6,747 行
kind 被连接器覆盖   521 行 → 证据留 NULL（诚实：该 kind 非分类器产出）
无证据              2,302 行
position_kind 不一致 242 行（nsz 的 title_raw 是缩写后的，分类器原本看的是全文）
```

复核队列 **616 条（6.8%）**：

| reason | 数量 | 含义 |
|---|---|---|
| `unclassified_academic` | 197 | 完全没匹配上规则，但整行看着像学术岗——**正是 T33/T35/T36 那类漏网的真实广告** |
| `excluded_by_rule` | 202 | 被"非学术岗"规则主动排除（真实决策，值得复核） |
| `low_confidence` | 217 | 规则命中了但很宽泛 |

抽样验证队列确实在捞真货：
- `Chemical and Process Engineering | Faculty Openings` —— 真实教职招聘，却未被分类
- `Part-Time Lab Assistant - Brady Lab`
- `Associate Professor in Design` 因正文出现 `hospitality` 被误排除 ← **正是该队列存在的意义**

## 5. 过程中发现并修正的三个真实缺陷

1. **区域折扣建模错误**：我一开始对 `classify_kind_intl` 也做了正文折扣，结果 1,296 条里有 **977 条**仅仅因为"在正文命中"就进队——那是噪声不是疑虑。`_ACADEMIC_POSITION` 的设计**本就是**扫标题+正文（"Faculty Position in Finance" 只在正文给出职称）。折扣只在有据可依处保留：`position_kind_intl`（KU DK 导师职称压过岗位的真实案例）。**这是修模型，不是调阈值。**
2. **入队用了重算的 kind 而非行上实际存储的 kind**：导致复核者看到 `classified_as=other` 而 listing 写着 `employment` 的自相矛盾条目。
3. **`title`/`body` 作用域错误**：在入队循环里读的是最后一行的残留值，直接清空了 `unclassified_academic` 整桶。

## 6. F16（上报，未擅自改动）

`rs_text.classify_kind(text, *, section_is_job_board=False)` —— 这个参数**全仓库只出现在签名处一次**，函数体从未使用。文档字符串却承诺了"板块本身是招聘板时可救回未分类文本"。

实现它会改变线上行的分类结果，那是产品决策而非 bug 修复，所以我没动。**请你裁：** ①按文档实现（未分类但板块是招聘板的会变可见）；②删掉参数+改文档（承认从未实现）。我倾向 ①——但这会让一批当前隐藏的行冒出来，得你拍。

## 7. 一个待你裁定的决策点（写入端点）

`record_decision()`（确认/纠正/驳回）已作为库函数交付并有测试覆盖，但**没有 HTTP 写入端点**，这是刻意的：

- 记录裁决是写操作，而本 API **目前完全没有鉴权**
- `/v1/applicants`、`/v1/thesis` 的写入端已经是裸奔状态（路线图 ④）——那是**未修的暴露面，不是可以照搬的先例**

**两个选项：**
- **A（推荐）**：等 ④ 鉴权落地时一起上，一次做完
- **B**：现在就上，用一个共享密钥 header 顶着

我选 A：为一个尚未上线的功能再开一个无鉴权写口，不划算。

## 8. 另一件事需要你确认

按协议我应该往 `pm/outbox/events.jsonl` 追加 JSONL 事件行。**但仓库里 `pm/` 目录根本不存在**，且 `git log --all` 显示它从未被提交过（`--diff-filter=A` 无任何 `pm/` 路径）。也就是说前几轮汇报里提到的 `events.jsonl`（189→192 行）和 F14 报告文件，在这个仓库里**没有落盘**。

请确认：PM 报文通道实际放在哪里？如果就是要用仓库里的 `pm/`，我下一轮把它建起来并把积压的事件补写进去。

## 9. 服务状态

- API `47820` ALIVE（`GET /v1/classification-review` 实测 200，返回 616）
- WEB 200 · scheduler `37988` ALIVE
- git `edb89fa` = `origin/main`，工作树干净

## 10. 下一步

仍开放：① 你亲自跑 `./tools/install_agents.sh`（沙箱里 `launchctl` 返回 EIO，唯一需你执行的未闭合项）；④ API 鉴权（写入端点的前置）；⑤ Dockerfile + CI；⑥ 多租户。
