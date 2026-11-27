.PHONY: install build dev mock typecheck

install:
	npm ci

build:
	npm run build
	npm run check-size

dev:
	npm run dev

mock:
	npm run dev:mock

typecheck:
	npx tsc --noEmit
