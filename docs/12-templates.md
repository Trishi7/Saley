# 12 · Templates (drafts)

Draft set. Sid and Vaishnavi replace the text later. Ids are stable. Placeholders come from facts only. The validator rejects any name, email, URL or date not in the input.

Sending is OFF: `EMAIL_SEND_ENABLED=false`. Drafts appear in the approval card. A human copies and sends.

| Id | Use | Subject | Body (≤ 90 words) |
| --- | --- | --- | --- |
| E1-lab-v1 | Email 1, AI lab / research role | Human preference data for {{topic}} | Hi {{first_name}}, I saw {{working_on_line}}. We run Pulse: verified students in India give fast, structured preference signals on model outputs, text, image or voice. Teams use it for evals and post-training data. Would a 20-minute look at a sample set for {{topic}} be useful? {{sender}} |
| E1-voice-v1 | Email 1, voice AI | Hinglish and Indic speech data | Hi {{first_name}}, {{company}} is building {{working_on_line}}. We collect Hinglish STT/TTS data and run A/B listening tests with verified speakers. A 10k-line Hinglish set is ready. Happy to share a sample. {{sender}} |
| E1-academic-v1 | Email 1, researcher | Free Pulse credits for {{topic}} research | Hi {{first_name}}, your work on {{paper_title}} is close to what we measure with Pulse. We offer research teams free credits to run preference studies with verified respondents. Would that help your next study? {{sender}} |
| E2-v1 | Email 2, day 4 | Re: {{subject_1}} | Hi {{first_name}}, a short follow-up. One concrete example: {{example_line}}. If this is not your area, who should I speak with? {{sender}} |
| E3-v1 | Email 3, day 12 | Re: {{subject_1}} | Hi {{first_name}}, last note from me. I will leave {{one_pager_link}} here. If timing changes, reply any time. {{sender}} |
| LI-note-v1 | LinkedIn note after accept | — | Thanks for connecting, {{first_name}}. I work on human preference data for AI teams. If {{topic}} is on your plate, I can share a sample. |
| RC-reason-v1 | Reconnect with a reason | Congrats on {{reason_short}} | Hi {{first_name}}, saw {{reason_line}}. If it opens a need for evals or speech data, I am around. {{sender}} |
| RC-plain-v1 | Reconnect, no reason | Checking in | Hi {{first_name}}, it has been a while. Anything on evals or data where we can help this quarter? {{sender}} |
| MA-v1 | Meeting ask | 20 minutes next week? | Hi {{first_name}}, would {{slot_1}} or {{slot_2}} work for a short call on {{topic}}? {{sender}} |

Placeholders: first_name, company, topic, working_on_line, paper_title, example_line, one_pager_link, reason_short, reason_line, slot_1, slot_2, sender, subject_1. Missing placeholder → draft is not produced; the card says which fact is missing.
