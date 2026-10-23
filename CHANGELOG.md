# [1.10.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.9.0...v1.10.0) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.20.0 ([d5de831](https://github.com/mini-app-polis/task-manager-cog/commit/d5de83140c57e6bcfd9ce75bf6623c1ce44e59a8))
* **deps:** require miniapppolis-common-utils 6.21.0 for 25-child splits ([049c45c](https://github.com/mini-app-polis/task-manager-cog/commit/049c45c5aa06a43cf174ba02f812ae2e853888cf))


### Features

* **split:** default the split cap to 25 children ([423589e](https://github.com/mini-app-polis/task-manager-cog/commit/423589e780aef052f9c8812de27a084775a8a1a6))

# [1.9.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.8.0...v1.9.0) (2026-10-23)


### Features

* **groomed:** record tasks groomed by hand in a working session ([dcd1981](https://github.com/mini-app-polis/task-manager-cog/commit/dcd19812a2bb072b5a1ace96e3a48e62fdcca08a))

# [1.8.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.7.0...v1.8.0) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.18.0 ([12dc287](https://github.com/mini-app-polis/task-manager-cog/commit/12dc2878cba05d861f56f0823c1cb60a93047557))
* **deps:** bump miniapppolis-common-utils to 6.19.0 ([c3f939f](https://github.com/mini-app-polis/task-manager-cog/commit/c3f939f0a98ad3d46de12dc6ef5b278c12a27073))


### Features

* **dispatch:** leave cowork and person tasks to their executors, and apply a proposed executor as a tag ([828964f](https://github.com/mini-app-polis/task-manager-cog/commit/828964f07b3a0300f96e9173b5e3fefc0eb5c54e))
* **groom:** create a split's child tasks and rewrite the task as their compound, behind a dry run and a cap ([e974892](https://github.com/mini-app-polis/task-manager-cog/commit/e974892f7597248477e8d12646863a4074f110e3))
* **lint:** hold tasks to a leaf or compound shape behind GROOM_TASK_SHAPES ([fc65915](https://github.com/mini-app-polis/task-manager-cog/commit/fc65915be899664d80bafd7cd411cc02124a436d))
* **results:** send a task back to Refining when its agent run needs grooming ([627df01](https://github.com/mini-app-polis/task-manager-cog/commit/627df01b2c749bd9e97369d761cdd1159395d761))

# [1.7.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.6.2...v1.7.0) (2026-10-23)


### Features

* **lint:** let Blocked by name a date the task waits for ([05cc825](https://github.com/mini-app-polis/task-manager-cog/commit/05cc825a8620a98e7993e5035cb9346af00cb80b))

## [1.6.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.6.1...v1.6.2) (2026-10-23)


### Bug Fixes

* **agent:** describe the GitHub response model fields ([ffd56d5](https://github.com/mini-app-polis/task-manager-cog/commit/ffd56d5c0949d7ca1f505d8c453239cc020bb45b))

## [1.6.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.6.0...v1.6.1) (2026-10-23)


### Bug Fixes

* **agent:** validate GitHub answers, retry transient failures, document dispatch ([a2afabc](https://github.com/mini-app-polis/task-manager-cog/commit/a2afabc42e0743016a609e1c4e5cf7935376023d))

# [1.6.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.5.4...v1.6.0) (2026-10-23)


### Features

* **agent:** dispatch Ready tasks to the agent and apply its results ([fa67ccd](https://github.com/mini-app-polis/task-manager-cog/commit/fa67ccdd1a7fdd066bb27c303346989b3c38680d))

## [1.5.4](https://github.com/mini-app-polis/task-manager-cog/compare/v1.5.3...v1.5.4) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.17.0 ([97fe5dc](https://github.com/mini-app-polis/task-manager-cog/commit/97fe5dc5bb4b975b24f42766965ec01cad425f62))
* **sweep:** don't send a task for grooming when unfinished blockers are its only problem ([055421e](https://github.com/mini-app-polis/task-manager-cog/commit/055421e2fcce625abe9ec48445cebfeb88f78010))

## [1.5.3](https://github.com/mini-app-polis/task-manager-cog/compare/v1.5.2...v1.5.3) (2026-10-22)


### Bug Fixes

* **sweep:** keep a task with unanswered grooming questions out of Ready ([e7a3695](https://github.com/mini-app-polis/task-manager-cog/commit/e7a36956ba57d0df836e17e976d3c781c236bc2d))

## [1.5.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.5.1...v1.5.2) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.2 ([af94624](https://github.com/mini-app-polis/task-manager-cog/commit/af94624c22f1ca3f639c7c70ce394c2ebc43e166))

## [1.5.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.5.0...v1.5.1) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.1 ([6a58999](https://github.com/mini-app-polis/task-manager-cog/commit/6a58999888a914aa031d506b06d14e0a4d70acf4))

# [1.5.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.4.2...v1.5.0) (2026-10-22)


### Features

* **sweep:** send the groomer the finished tasks a task mentions ([834ff12](https://github.com/mini-app-polis/task-manager-cog/commit/834ff12867137528552fc88c2fdd4662069d089a))

## [1.4.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.4.1...v1.4.2) (2026-10-22)


### Bug Fixes

* **groom:** leave a task with questions in Refining, not Blocked ([b84d0a3](https://github.com/mini-app-polis/task-manager-cog/commit/b84d0a3ebc1a6781317a1f71faebf45fb97723ca))

## [1.4.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.4.0...v1.4.1) (2026-10-22)


### Bug Fixes

* **registry:** read the ecosystem registry again on a transient failure ([2a2a199](https://github.com/mini-app-polis/task-manager-cog/commit/2a2a19968fca423d95d70f29430b2df76913a30d))
* **sweep:** log the read-only report and document the public definitions ([ec764c2](https://github.com/mini-app-polis/task-manager-cog/commit/ec764c233968e873fa1648c681228378c8f64ff2))

# [1.4.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.3.0...v1.4.0) (2026-10-22)


### Features

* **compose:** fill the template fields a draft knows ([f526795](https://github.com/mini-app-polis/task-manager-cog/commit/f526795c927176bfb36f0d239a4cfcfe89184fce))
* **compose:** fill the template fields a draft knows ([2e38860](https://github.com/mini-app-polis/task-manager-cog/commit/2e38860974d2e764ddc982b5123bb99e3f152851))
* **sweep:** leave detected problems for a person, neither groomed nor moved to Ready ([7a4fb3f](https://github.com/mini-app-polis/task-manager-cog/commit/7a4fb3ff5ba43ba4583e0dc841a1500245c95624))

# [1.3.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.2.0...v1.3.0) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.14.0 ([ef346ec](https://github.com/mini-app-polis/task-manager-cog/commit/ef346ec084bd4efdcd1a6ad20ee838da9e24f0bd))
* **deps:** bump miniapppolis-common-utils to 6.15.1 ([67a2bef](https://github.com/mini-app-polis/task-manager-cog/commit/67a2bef7f7a7b0fabbea0652181c0d49905cdbe5))
* **lint:** read a stray label inside the template as body, and split Where on semicolons ([dbe2e65](https://github.com/mini-app-polis/task-manager-cog/commit/dbe2e652950f803ce070bd7dcbb434d1a26f7a5e))


### Features

* **groom:** send tasks that are not ready to the groomer, and apply its proposals ([abdc09f](https://github.com/mini-app-polis/task-manager-cog/commit/abdc09f8061d5ef95acd18b6e5d9cf9f1e064ea4))
* **sweep:** keep each task's grooming record, and count tasks sent back from Ready ([16d7c31](https://github.com/mini-app-polis/task-manager-cog/commit/16d7c311c0313505a9333500fc376d0adc82e97e))

# [1.2.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.1.2...v1.2.0) (2026-10-21)


### Features

* **sweep:** check the board hourly against the definition of ready, and move ready tasks to Ready ([66fc9f3](https://github.com/mini-app-polis/task-manager-cog/commit/66fc9f3a2fc17e7e9bdcc6ea5fe670fa33b6c613))
* **sweep:** check the board hourly against the definition of ready, and move ready tasks to Ready ([772cab3](https://github.com/mini-app-polis/task-manager-cog/commit/772cab38989f4a472c7d20045be91ca60daaf172))

## [1.1.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.1.1...v1.1.2) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.11.2 ([4f7ee99](https://github.com/mini-app-polis/task-manager-cog/commit/4f7ee99ffbaee8f3c80760b17f0fdc96e5672e3b))

## [1.1.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.1.0...v1.1.1) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.11.1 ([6fc0ac6](https://github.com/mini-app-polis/task-manager-cog/commit/6fc0ac6e6b2141d5bc556d909e5d0246a735176f))

# [1.1.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.0.2...v1.1.0) (2026-10-21)


### Features

* sources for detected problems (CI, RUN, CONF, API) ([9c7b38d](https://github.com/mini-app-polis/task-manager-cog/commit/9c7b38d7abb71669014e651d25d8f86947846b8e))

## [1.0.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.0.1...v1.0.2) (2026-10-20)


### Bug Fixes

* respx for HTTP mocking, Board docstrings, README running-locally section ([7c74c02](https://github.com/mini-app-polis/task-manager-cog/commit/7c74c02ae1e229e5378338817242fc394d2719e7))

## [1.0.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.0.0...v1.0.1) (2026-10-20)


### Bug Fixes

* load SSM secrets before the worker starts Sentry ([19a2bb6](https://github.com/mini-app-polis/task-manager-cog/commit/19a2bb61e04813fcf408f5b5039dbbb781543aff))

# 1.0.0 (2026-10-20)


### Features

* task-manager-cog worker ([d52ea85](https://github.com/mini-app-polis/task-manager-cog/commit/d52ea854a320173002a73cee7c61400476621229))

# Changelog
