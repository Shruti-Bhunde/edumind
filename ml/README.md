# Datasets for training the EduMind models

Links below were opened and checked on 2026-10-16/17. Where a page did not state something
(a license, the exact columns) that is said explicitly, so check it yourself before relying on it.
Several need a free account or acceptance of rules. **Read each license before use**: several are
non-commercial, and one forbids derivatives.

## A. Best fits for the misconception classifier

| Dataset | What it is | Access | Fit |
|---|---|---|---|
| **MIME: Misunderstandings in Math Explanations**<br>https://github.com/scrosseye/mime_dataset<br>Paper: https://aclanthology.org/2026.aimecon-main.33.pdf | 52,463 student explanations (38,095 real + 14,368 synthetic) for 15 multiple-choice maths questions, grades 4-8, from Eedi. Topics: rational numbers, equations and functions. Each explanation is labelled correct / misunderstanding / neither, plus the misunderstanding type. EDM 2026 best public dataset award. | Public GitHub repo. The paper states no license: check the repo. | **Best match.** Free-text "why I answered that" mapped to misunderstanding labels. This is what your multiple-choice follow-ups approximate. Would let you replace follow-ups with a text classifier. |
| **Eedi: Mining Misconceptions in Mathematics** (Kaggle)<br>https://www.kaggle.com/competitions/eedi-mining-misconceptions-in-mathematics | Maths multiple-choice questions where each wrong option is tied to a misconception description. Task: match a wrong answer to the right misconception text. | Kaggle account, accept the competition rules, download from the Data tab. | Gives hundreds of reviewed misconception descriptions and their wrong answers. `python -m ml.eedi_baseline` is written for it (use `--fractions-only`). |
| **MAP: Charting Student Math Misunderstandings** (Kaggle)<br>https://www.kaggle.com/competitions/map-charting-student-math-misunderstandings | Task: "predict the affinity between misconceptions and student open-ended responses". The EDM awards page names MIME as this competition's data. | Kaggle account and rules. I could not read its Data page, so confirm the files and license there. | Same family as MIME, in competition format with a leaderboard to compare against. |
| **MalruleLib**<br>https://github.com/luffycodes/malrulelib | Code that turns documented misconceptions into executable procedures: 101 "malrules" over 498 problem templates, producing correct and misconception-consistent step traces. Includes fraction errors such as adding numerators and denominators, subtracting across, and comparing by numerator or denominator only. | Public repo. The paper states no license: check the repo. | Replaces my small simulator (`ml/synthetic.py`) with a much richer, published one. Synthetic, so it still isn't real students. |

## B. For mastery tracking and the review scheduler (not misconception labels)

| Dataset | What it is | Access |
|---|---|---|
| **NeurIPS 2020 Education Challenge (Eedi)**<br>https://eedi.com/projects/neurips-education-challenge<br>Paper: https://arxiv.org/abs/2007.12061 | Tasks 1-2: 27,613 questions, 118,971 students, 15,867,850 answers. Tasks 3-4: 948 questions, 4,918 students, 1,382,727 answers. Four-option maths questions with question, student and answer metadata. EDM 2021 best dataset award. | Download links are on the project page. **CC BY-NC-ND 4.0**: non-commercial, no derivatives. |
| **ASSISTments data**<br>https://sites.google.com/site/assistmentsdata/home<br>Also (EDM 2022 award): https://osf.io/59shv | Student logs from the ASSISTments maths tutor: 2009-10 skill builder, 2012-13 with affect, 2015 skill builder, and more. | Read the site's Terms of Use first. Separately, the SafeInsights "ASSISTments data lake" is **restricted** (application required). |
| **EdNet**<br>https://github.com/riiid/ednet | 131,441,538 interactions from 784,309 students of an AI tutoring app (KT1-KT4 levels). | Zip downloads linked from the README. **CC BY-NC 4.0**. As far as I know it is English test-prep, not maths, so it is useful only for forgetting/review modelling. |
| **KDD Cup 2010 (PSLC DataShop)**<br>https://pslcdatashop.web.cmu.edu/KDDCup/ | Five datasets (3 development, 2 challenge) from an educational-software challenge. | Free account required, and you must cite it. The page did not list file details. |

## C. For LLM-written explanations (later)

| Dataset | What it is | Access |
|---|---|---|
| **MathDial**<br>https://huggingface.co/datasets/eth-nlped/mathdial<br>Paper: https://arxiv.org/abs/2305.14536 | 2.86k tutoring dialogues (train 2,260 / test 599) about maths word problems. Fields include `student_incorrect_solution`, `teacher_described_confusion` and `conversation`. | Open, **CC BY 4.0**, no gate. `datasets.load_dataset("eth-nlped/mathdial")` |

Index of award-winning education datasets: https://educationaldatamining.org/data-set-awards/

## Honest notes on using them

* **Label mismatch.** These datasets use their own misconception names. Mapping them to
  M01-M06 is manual work: for example whole-number bias is M03, and adding numerators and
  denominators is M01. Keep only the rows that map cleanly, and say so in your report.
* **No dataset here is a drop-in for this exact task.** The most direct are MIME and MAP
  (free-text explanation to label). Eedi gives wrong answers to misconception text.
* **Evaluate on unseen questions.** `ml/train.py` already splits by question. Do the same
  with real data, and report macro-F1 per class, not just accuracy.
* **Licenses.** NC means non-commercial. ND means no derivatives, so check whether a trained
  model counts for your use. Do not redistribute the data itself.
* **Privacy.** Do not mix real student data with your learners' data. Pilot data collected with
  EduMind needs consent first.

## Using the scripts

```bash
python -m ml.train --n 20000                          # simulated data, runs anywhere
python -m ml.eedi_baseline --data-dir ~/data/eedi --fractions-only
```

`ml/eedi_baseline.py` expects `train.csv` and `misconception_mapping.csv` with columns such as
`QuestionId, ConstructName, SubjectName, QuestionText, CorrectAnswer, AnswerAText..AnswerDText,
MisconceptionAId..MisconceptionDId` and `MisconceptionId, MisconceptionName`. I wrote these from
the public competition description and could not download the files here (login required), so
the script checks the columns and prints exactly what differs if Kaggle's format is different.
It is tested only on a tiny fabricated file with the same columns.
