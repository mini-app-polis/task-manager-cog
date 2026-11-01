## [1.25.4](https://github.com/mini-app-polis/task-manager-cog/compare/v1.25.3...v1.25.4) (2026-11-01)


### Bug Fixes

* **review:** flag an unverified Review task once with a comment instead of on every sweep ([413f8c5](https://github.com/mini-app-polis/task-manager-cog/commit/413f8c510859f4f6f1f1ebd2d6ec15ec1d3d11f2))

## [1.25.3](https://github.com/mini-app-polis/task-manager-cog/compare/v1.25.2...v1.25.3) (2026-11-01)


### Bug Fixes

* **deps:** bump the minor-and-patch group with 2 updates ([3c05bdf](https://github.com/mini-app-polis/task-manager-cog/commit/3c05bdf20d37c9a167a8431326ff0d1d326365bc))
* **update:** find a recovery's task by scanning the board when the external-id lookup misses, and report a miss as an issue ([ef202f2](https://github.com/mini-app-polis/task-manager-cog/commit/ef202f2b82129e90aaa72ce23c2b6b8129fde442))

## [1.25.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.25.1...v1.25.2) (2026-11-01)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.8.2 ([cff6e88](https://github.com/mini-app-polis/task-manager-cog/commit/cff6e8806bca9c26caa23f10828b93e3a5e03ca1))

## [1.25.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.25.0...v1.25.1) (2026-11-01)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.8.1 ([06e852f](https://github.com/mini-app-polis/task-manager-cog/commit/06e852fbe656b7a2febf4b27608b7916c7a660fe))

# [1.25.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.24.2...v1.25.0) (2026-10-31)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.8.0 ([5ab8d61](https://github.com/mini-app-polis/task-manager-cog/commit/5ab8d6151cf72d6b1f2618932e16ad2b820a292b))


### Features

* **worker:** log effective settings once at cold start, values only for allowlisted names ([0fe4451](https://github.com/mini-app-polis/task-manager-cog/commit/0fe4451ba78e02a25f800741ac15411ea1c7178d))

## [1.24.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.24.1...v1.24.2) (2026-10-31)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.7.0 ([f3f1afc](https://github.com/mini-app-polis/task-manager-cog/commit/f3f1afcf6b084a90d4f35bdc6a303ede806d6093))
* **deps:** patch vulnerable dependencies (pyasn1) ([e049280](https://github.com/mini-app-polis/task-manager-cog/commit/e04928038ec247dc7d8270f754d932d02f805403))

## [1.24.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.24.0...v1.24.1) (2026-10-30)


### Bug Fixes

* **split:** stop flagging a child as waiting when its text says other children wait on it ([fe911d5](https://github.com/mini-app-polis/task-manager-cog/commit/fe911d512f24d6435d5a18c9e9e8a0adf310e8e8))

# [1.24.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.23.0...v1.24.0) (2026-10-30)


### Features

* **sweep:** add a sweep-now workflow to ask for a sweep of the board from the Actions tab ([0ddde6d](https://github.com/mini-app-polis/task-manager-cog/commit/0ddde6d25d5d85aac226c8d7d34f07c70a59cec5))

# [1.23.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.22.0...v1.23.0) (2026-10-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.6.0 ([eb75635](https://github.com/mini-app-polis/task-manager-cog/commit/eb75635d53b02226290620738bf119af6daa75c3))
* **review:** count a Handed off: comment posted just before a task moved to Review ([d66be8e](https://github.com/mini-app-polis/task-manager-cog/commit/d66be8e24cd6ca79a55bc43cafe4ad432ab026ff))


### Features

* **sweep:** honour groom/agents flags on an on-demand task.sweep and say so in the run report ([9acaeb0](https://github.com/mini-app-polis/task-manager-cog/commit/9acaeb0e2476db21a4402c1ef040b0a3bf28b1c8))
* **sweep:** honour groom/agents flags on an on-demand task.sweep and say so in the run report ([4698072](https://github.com/mini-app-polis/task-manager-cog/commit/4698072dec4ceedfd3588963ebc1e12db39ea9b4))

# [1.22.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.21.1...v1.22.0) (2026-10-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.4.0 ([1881f6c](https://github.com/mini-app-polis/task-manager-cog/commit/1881f6c7a4eddfd7a9c7e73f8852ac6b2e58e9a4))
* **deps:** bump miniapppolis-common-utils to 7.5.0 ([65e7a38](https://github.com/mini-app-polis/task-manager-cog/commit/65e7a38b126036f4afa28d2141ccb6385971bc60))


### Features

* **sweep:** post Landed: for Review tasks whose pull request merged with green CI on dev ([b0340a1](https://github.com/mini-app-polis/task-manager-cog/commit/b0340a1e020fe838a93a9fbf8587dd8e17170627))

## [1.21.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.21.0...v1.21.1) (2026-10-30)


### Bug Fixes

* **results:** start a succeeded agent run's comment with "Handed off:" and record the Landed: closing rule ([b7d50ac](https://github.com/mini-app-polis/task-manager-cog/commit/b7d50acb2ab506a86bfedad27b7736d22f15dff4))

# [1.21.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.20.0...v1.21.0) (2026-10-30)


### Bug Fixes

* **results:** link a succeeded run's report line to its draft PR ([cd0703e](https://github.com/mini-app-polis/task-manager-cog/commit/cd0703e2bb39a2fbcf5702f4643e85460cc41b01))
* **sweep:** label dispatch skip reasons in plain words in the groom-sweep report ([ed1af81](https://github.com/mini-app-polis/task-manager-cog/commit/ed1af811029dddf5fc92344a201a1607135bce23))


### Features

* **lint:** let a cowork or person task name more than one repository in Where ([2506d57](https://github.com/mini-app-polis/task-manager-cog/commit/2506d57a0c2c1fcfbe8e388243ee1574608a4dd8))

# [1.20.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.19.2...v1.20.0) (2026-10-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.2.0 ([e536796](https://github.com/mini-app-polis/task-manager-cog/commit/e5367960b6871e22c3d615af1e31ed0bc1ab227c))
* **deps:** bump miniapppolis-common-utils to 7.3.0 ([97ff627](https://github.com/mini-app-polis/task-manager-cog/commit/97ff6272cd1987b3a58c8bfe2710047f534124c4))


### Features

* **lint:** require the Executor line in the definition of ready ([aabe65e](https://github.com/mini-app-polis/task-manager-cog/commit/aabe65e31ee1d52e0f5d70c02b7bea25a16b4e2e))
* **sweep:** complete Review tasks whose latest comment starts with "Landed:" and report unverified ones ([851b164](https://github.com/mini-app-polis/task-manager-cog/commit/851b164926ea93953dbfe4e549547f8246ebbfea))
* **update:** move a secret's open rotation task to Review when an update names its key prefix ([9472ef0](https://github.com/mini-app-polis/task-manager-cog/commit/9472ef04b04bb235a4961534895a75cd2b6da3e1))

## [1.19.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.19.1...v1.19.2) (2026-10-30)


### Bug Fixes

* **executor:** assign every task to the owner, whoever does the work ([f709ffa](https://github.com/mini-app-polis/task-manager-cog/commit/f709ffad4d195747c1cb98bb453a22c49d55e141))

## [1.19.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.19.1...v1.19.2) (2026-10-30)


### Bug Fixes

* **executor:** assign every task to the owner, whoever does the work ([f709ffa](https://github.com/mini-app-polis/task-manager-cog/commit/f709ffad4d195747c1cb98bb453a22c49d55e141))

## [1.19.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.19.0...v1.19.1) (2026-10-30)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 7.1.1 ([81a4492](https://github.com/mini-app-polis/task-manager-cog/commit/81a4492bfcd7dc71473601d4469919b355027432))

# [1.19.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.18.2...v1.19.0) (2026-10-29)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.34.0 ([f3b9cc0](https://github.com/mini-app-polis/task-manager-cog/commit/f3b9cc039ce20647a6a0b81ae63dda4b64b0435d))
* **deps:** bump miniapppolis-common-utils to 6.35.0 ([e0bbde3](https://github.com/mini-app-polis/task-manager-cog/commit/e0bbde374eff3b64c3d842d4d0f33a7bedf1b929))
* **deps:** bump miniapppolis-common-utils to 7.1.0 ([594028b](https://github.com/mini-app-polis/task-manager-cog/commit/594028b29a79e852882d4848e945336638638763))


### Features

* **executor:** an Executor line on every task, kept in step with its tag and assignment by the sweep ([7b8960d](https://github.com/mini-app-polis/task-manager-cog/commit/7b8960dfc138a5f7fdd315a6599fe5743b1d66f5))
* **sources:** accept manual tasks filed by hand, on common-python-utils 7.x ([c8b8c00](https://github.com/mini-app-polis/task-manager-cog/commit/c8b8c00e6110dae8289540ae967da0d04a8256aa))
* **sweep:** report in Moved, Board and Agent sections, with the 30-day return-from-Ready ratio ([76be57f](https://github.com/mini-app-polis/task-manager-cog/commit/76be57fdc79c7a84db2e50a78ddfd0ebf89ed219))

## [1.18.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.18.1...v1.18.2) (2026-10-29)


### Bug Fixes

* **dispatch:** start no agent task with under 30 s left before the run deadline ([da7c61d](https://github.com/mini-app-polis/task-manager-cog/commit/da7c61d6192fe039d4f611f70003e3b0433cb6a7))

## [1.18.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.18.0...v1.18.1) (2026-10-28)


### Bug Fixes

* **doc-004:** list GROOM_TIMES, AGENT_TIMES and SWEEP_TIMEZONE in .env.example ([ca7c07f](https://github.com/mini-app-polis/task-manager-cog/commit/ca7c07ff896c076ad22d5587aa5fa804cbc84c64))

# [1.18.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.17.0...v1.18.0) (2026-10-28)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.33.0 ([a67c0e9](https://github.com/mini-app-polis/task-manager-cog/commit/a67c0e9799fd7932903f29b88e2b892791470a0a))


### Features

* **sweep:** run grooming and agent dispatch only at GROOM_TIMES and AGENT_TIMES, read in SWEEP_TIMEZONE ([0c0dde5](https://github.com/mini-app-polis/task-manager-cog/commit/0c0dde5bbd5c66d99c5e6c654ea82e05d6a1688e))
* **sweep:** send a grooming again when it never comes back, up to three sends ([14616aa](https://github.com/mini-app-polis/task-manager-cog/commit/14616aac51559f9089df096f0032aa271c72680b))
* **sweep:** send grooming only in the hours GROOM_HOURS names, read in GROOM_TIMEZONE ([8526205](https://github.com/mini-app-polis/task-manager-cog/commit/852620554014855ca467f8b2378d779fc01bf672))

# [1.17.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.16.0...v1.17.0) (2026-10-28)


### Features

* **sweep:** send a task again when its grooming was stopped by the budget ([c998780](https://github.com/mini-app-polis/task-manager-cog/commit/c998780e24ca83f6e3d015f2fc60ae4c0e1812f5))

# [1.16.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.15.2...v1.16.0) (2026-10-28)


### Features

* **groom:** comment the lint problems on a groomed task left not ready ([99e3839](https://github.com/mini-app-polis/task-manager-cog/commit/99e3839ca73c3b29cc9a97189a8887828306fc3e))

## [1.15.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.15.1...v1.15.2) (2026-10-28)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.31.0 ([eedfedb](https://github.com/mini-app-polis/task-manager-cog/commit/eedfedb84cc00d312694742f89b70dfcfe622f90))

## [1.15.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.15.0...v1.15.1) (2026-10-26)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.30.0 ([82c42ff](https://github.com/mini-app-polis/task-manager-cog/commit/82c42ff7790486dc968c2984ee171d0163a3c1bb))

# [1.15.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.14.3...v1.15.0) (2026-10-26)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.28.0 ([e7739c2](https://github.com/mini-app-polis/task-manager-cog/commit/e7739c2f118ead80704c4815423cbc4aa9419e25))


### Features

* **sources:** mint ROT- ids for secret-rotation tasks and leave them out of the sweep ([a487bb5](https://github.com/mini-app-polis/task-manager-cog/commit/a487bb50eb3c156240b4d31631fe981113ad7d19))

## [1.14.3](https://github.com/mini-app-polis/task-manager-cog/compare/v1.14.2...v1.14.3) (2026-10-26)


### Bug Fixes

* **sweep:** name a held task as a notice on the sweep that first finds it held, not as a WARN on every sweep ([d9fdf1f](https://github.com/mini-app-polis/task-manager-cog/commit/d9fdf1f77231b55692f9a43ed02694e610938297))

## [1.14.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.14.1...v1.14.2) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.26.0 ([2c6fa44](https://github.com/mini-app-polis/task-manager-cog/commit/2c6fa447ee69765787cb2f41f2c0e10a2c32368f))

## [1.14.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.14.0...v1.14.1) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.25.0 ([bc5bd1a](https://github.com/mini-app-polis/task-manager-cog/commit/bc5bd1a8743d4d3d6d25ff0817a16d080bd8caa1))

# [1.14.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.13.3...v1.14.0) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.24.0 ([7268047](https://github.com/mini-app-polis/task-manager-cog/commit/72680476d5f6920e2fc4ef66df4e3ce8fc9963b6))
* **groom:** post a split proposal's questions instead of applying it ([72c73b2](https://github.com/mini-app-polis/task-manager-cog/commit/72c73b2573755596475fa45362d78da0e6e38280))
* **split:** refuse a split whose child says it waits but names no blocker ([0c497bb](https://github.com/mini-app-polis/task-manager-cog/commit/0c497bb9bd8d5fc884fb015389724ec861d41352))


### Features

* **sweep:** name tasks held with nobody asked in the run report ([4e14742](https://github.com/mini-app-polis/task-manager-cog/commit/4e14742e0fa6d41270e33c3fefb7b926c405f03d))
* **update:** handle task.update by commenting on a task and moving it to a named column ([02dd9e0](https://github.com/mini-app-polis/task-manager-cog/commit/02dd9e09e2760f93fa9babc840c39410a3dc6e23))

## [1.13.3](https://github.com/mini-app-polis/task-manager-cog/compare/v1.13.2...v1.13.3) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.23.0 ([8b3314c](https://github.com/mini-app-polis/task-manager-cog/commit/8b3314c657d232c51bf1606a1257c644a81ccbf1))

## [1.13.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.13.1...v1.13.2) (2026-10-25)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.3 ([45217cb](https://github.com/mini-app-polis/task-manager-cog/commit/45217cb36608fea9a839e7b19d5f823be015c9b1))

## [1.13.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.13.0...v1.13.1) (2026-10-25)


### Bug Fixes

* **split:** a reported split waits on its owner, and an answer re-grooms it ([3681da0](https://github.com/mini-app-polis/task-manager-cog/commit/3681da01d1c9b3a5e1c5288908a64d57fe682405))

# [1.13.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.12.0...v1.13.0) (2026-10-25)


### Features

* **sweep:** re-lint Ready and send a task that no longer passes back to Refining ([48fc864](https://github.com/mini-app-polis/task-manager-cog/commit/48fc8646dc91736f3833517a8f82525f657ef9c8))

# [1.12.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.11.0...v1.12.0) (2026-10-24)


### Features

* **sweep:** hold a task out of Ready while a Grooming questions comment is unanswered ([5ccdc60](https://github.com/mini-app-polis/task-manager-cog/commit/5ccdc60d1b00d6c4b3a4b2ec966d1e73180131b3))

# [1.11.0](https://github.com/mini-app-polis/task-manager-cog/compare/v1.10.2...v1.11.0) (2026-10-24)


### Features

* **sweep:** move tasks waiting only on blockers to Blocked and fail tasks whose body lists open questions ([9cbc05f](https://github.com/mini-app-polis/task-manager-cog/commit/9cbc05f970b764eaadc30de8b8abcc753bd66b3d))

## [1.10.2](https://github.com/mini-app-polis/task-manager-cog/compare/v1.10.1...v1.10.2) (2026-10-24)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.1 ([42c6765](https://github.com/mini-app-polis/task-manager-cog/commit/42c6765a9e764d358e05a1a6c4581f15293fa3e2))
* **deps:** bump miniapppolis-common-utils to 6.22.2 ([fe6a3ad](https://github.com/mini-app-polis/task-manager-cog/commit/fe6a3ad4c667e266b345c5fcded31f7af0a8ae52))

## [1.10.1](https://github.com/mini-app-polis/task-manager-cog/compare/v1.10.0...v1.10.1) (2026-10-23)


### Bug Fixes

* **worker:** release the move onto the shared Lambda worker module and queue message models ([45cd141](https://github.com/mini-app-polis/task-manager-cog/commit/45cd1410f3bdd8d68baa597b01cf087b8e06f602))

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
