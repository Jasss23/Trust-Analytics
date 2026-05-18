# Trust Analytics Workflow Speaker Notes

## Narrative Spine

The story is not "I built a dashboard."

The story is: I had the same problem many business teams have with AI analytics. AI can produce SQL, charts, and confident recommendations, but people still hesitate to act because they cannot see the proof path. I turned that trust problem into a product workflow. The workflow gives every AI-generated answer a clear status, the evidence behind it, and the next safe action. The key lesson is that UX is not decoration around the LLM. UX is part of how we make the LLM useful.

## Timing

- Main story, slides 1 to 9: 7 to 9 minutes.
- Technical appendix, slides 10 to 12: 3 to 5 minutes, or use only if asked.
- Do not read the slide title first. Open each slide with the reason it matters.

## Slide 1: Trust Analytics Workflow

Start with the problem, not the product name.

"The question I started with was simple: when AI gives us a data analysis, how do we know whether we can actually act on it?"

"In this project, I designed a workflow for AI-generated analysis. The goal is not just to make the answer faster. The goal is to make the answer safe enough for a business decision."

"The pattern is: ask a business question, validate the data path, then decide whether to use it, review it, or hand it off."

Transition: "Let me make the trust problem concrete."

## Slide 2: The Trust Gap

Use the fintech example as the anchor.

"Imagine a multi-asset trading platform planning next month's promotion budget. They offer crypto, gold, FX, GSS, and options. The business question is: which asset class should we prioritise?"

"AI can generate SQL and a chart very quickly. It might say crypto has the strongest completed GTV. That is useful, but it is not enough."

"Before a team acts on that answer, they need to know basic things: which table did it use, did it exclude pending transactions, and do finance and operations sources agree?"

"That gap between a generated answer and a decision-ready answer is the trust gap."

Transition: "My first instinct was the same as many product people: can a better chatbox solve this?"

## Slide 3: Chatbox Alone

Make the tension clear.

"The ideal interface would be one chatbox. You ask once, and the model infers the metric, source, SQL, chart, warning, and recommendation."

"That is the dream, and it is still directionally right. Chat is a great entry point."

"But in production, trusted analysis needs control points. The model can pick the wrong table, miss a source conflict, or sound confident even when data definitions disagree."

"So I stopped treating chat as the whole product. I treated the LLM as the engine, and designed a workflow around it."

Transition: "Here is the workflow I built around that engine."

## Slide 4: Workflow Overview

Do not explain every card. Explain the control system.

"This is the core product design. The workflow takes a plain-English business question and turns it into a controlled path."

"First, we capture the question. Then we shape the intent so the system knows the metric, period, and source policy. Then we validate the answer with read-only SQL, QA checks, and source comparison. Finally, we route the result."

"The important design choice is the routing. The model does not just return text. The product decides which screen the answer belongs on."

"Safe answers go to a decision view. Answers that need checking go to evidence view. Blocked answers go to audit handoff."

Transition: "The routing decision is the part I would spend the most time on with a client."

## Slide 5: Routing Close-Up

Make the state machine feel practical.

"Here is the same idea closer up. The answer is not just 'crypto is best'. The answer has a trust state."

"If validation passes, the product can send the decision view. If the analysis needs evidence, it sends the analyst to SQL, source choice, and QA notes. If there is a source conflict, it stops the decision and creates a handoff."

"This matters because trust is no longer a feeling. It becomes a product path."

"The user does not have to decide from a raw model response. The workflow tells them what action is safe."

Transition: "Once I had that routing model, the UI split became obvious."

## Slide 6: Three Views

Frame this as the main design decision.

"The same analysis needs to answer three different human questions."

"The business owner asks: can I act on this? They need a plain-English conclusion, the boundary, and the warning."

"The analyst asks: can I defend this? They need SQL, source rationale, reconciliation, QA, and challenge notes."

"Risk or compliance asks: should this be blocked? They need the conflict, the owner path, and the resolution checklist."

"This is why I did not build one dashboard with more tabs. I built different product surfaces for different trust states."

Transition: "Then I turned those surfaces into the working product."

## Slide 7: Product Proof

Use screenshots as evidence, not decoration.

"These are the actual product surfaces that come out of the workflow."

"The decision view packages the recommendation in business language. It still carries the warning and the boundary, so it is not overselling the result."

"The evidence view keeps the analysis defensible. It exposes the SQL, the source choice, the reconciliation, and the QA trail."

"The audit handoff is the safety valve. If sources disagree, the product blocks leadership packaging and names the next step."

"So the workflow is not abstract. It becomes decisions, evidence, and escalation."

Transition: "The deeper lesson is that UX changes how useful the model can be."

## Slide 8: UX As Control System

Make the thesis explicit.

"A common way to think about AI product work is: the model gets smarter, the UI gets simpler."

"I think that is only half true. In high-trust workflows, good UX actually improves model performance because it narrows the task."

"Question shaping gives the model clearer inputs. SQL pre-flight catches mismatches before users see the result. Source reconciliation makes disagreement visible. Audit handoff stops confident guesses from becoming business decisions."

"That is why UX and LLM quality are connected. A smaller or older model can still be useful if the workflow gives it the right job and the right boundaries."

Transition: "So the operating lesson is bigger than this fintech case."

## Slide 9: Operating Lesson

Close the main story.

"The lesson I took from this project is that the product has to earn trust before analysis can drive action."

"Chat is powerful, but it is not the whole interface for trusted data work."

"The best UX makes the model more useful by reducing ambiguity before the answer reaches the user."

"And the pattern transfers. Anywhere AI analysis needs to become a business decision, the product needs answer, evidence, action, and escalation."

"That is the main story. The appendix shows how I made it production-grade."

Transition: "I can quickly show the technical design behind it."

## Slide 10: Appendix A, Architecture

Use this only if the audience wants implementation depth.

"The key architecture decision is that the UI does not consume raw LLM text."

"The model output is projected into a structured analysis object. That object carries status, evidence, warnings, and export metadata."

"The input layer shapes the business question. The agent chain plans, writes SQL, and executes read-only queries. The trust layer checks source fit, reconciles sources, and creates the trust state. The product contract decides which view can render."

"That separation is what makes the workflow safer. The UI is not trusting prose. It is trusting a controlled contract."

Transition: "The call logic is where this contract becomes behavior."

## Slide 11: Appendix B, Call Logic

Emphasise explicit failure and fallback.

"The API path is intentionally simple. Shape the request, run the analysis, route by status, then package the output."

"The most important part is not the happy path. It is what happens when the system cannot validate the answer."

"There is no silent fallback. If live validation is unavailable, the product can show a verified cached result only when it is clearly labeled as cached and carries the original evidence."

"That is important because fallback is often where trust quietly breaks."

Transition: "Finally, these are the guardrails that make it production-grade rather than just a prototype."

## Slide 12: Appendix C, Guardrails

End with build credibility.

"These are the implementation choices that make the workflow credible."

"SQL is read-only. Pre-flight checks source, filters, aggregation, and answer shape. Source reconciliation names conflicts instead of hiding them. Audit-first failure mode gives users a handoff path instead of a confident guess."

"Exports carry the same warnings and evidence trail, so trust does not disappear when the answer becomes a CSV, email, or slide. Telemetry tracks usage so the workflow can improve."

"The design is narrow where trust matters and flexible where users need output."

## Final Close

"The product lesson is that reliable AI analytics is not only a model problem. It is a workflow problem."

"The model generates the answer. The product decides whether the answer is safe to use, needs review, or must be stopped."

"That is the difference between AI that produces analysis and AI that can support real business decisions."
