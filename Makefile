.PHONY: up down migrate lint test test-int smoke verify-p1 verify-p2 verify-p3 verify-p4 verify-p5 verify-p6 verify-p6-chem serve

UV ?= uv

up:
	docker compose up -d --wait

down:
	docker compose down

# 数据库迁移（DATABASE_URL；未配置时为本地 SQLite ./data/drugpaper.db）
migrate:
	$(UV) run drugpaper db upgrade

lint:
	$(UV) run ruff check drugpaper tests scripts
	$(UV) run ruff format --check drugpaper tests scripts
	$(UV) run mypy

# 单元测试：不访问任何外部服务
test:
	$(UV) run pytest tests/unit -q

# 集成测试：PostgreSQL。TEST_DATABASE_URL 未设置时使用嵌入式 pgserver（dev 依赖）
test-int:
	$(UV) run pytest tests/integration -q -m integration

# 真实模型端点冒烟测试：需要 .env 中的 LLM_* 配置
smoke:
	$(UV) run pytest tests/smoke -q --smoke

verify-p1:
	$(UV) run python scripts/verify_phase_1.py

verify-p2:
	$(UV) run python scripts/verify_phase_2.py

verify-p3:
	$(UV) run python scripts/verify_phase_3.py

verify-p4:
	$(UV) run python -m scripts.verify_phase_4

serve:
	$(UV) run drugpaper serve

verify-p5:
	$(UV) run python -m scripts.verify_phase_5

verify-p6:
	$(UV) run python -m scripts.verify_phase_6 --full

verify-p6-chem:
	$(UV) run python -m scripts.verify_phase_6_chemistry
