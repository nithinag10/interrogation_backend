# Interrogation Agent API Contract

Base URL (local): `http://localhost:8000`

Content type:
- JSON for REST APIs
- `text/event-stream` for SSE

## 1. Stakeholder Catalog

Use this to fetch predefined stakeholder profiles from `data/stakeholders.json`.

### GET `/api/stakeholders`

Response `200`:

```json
{
  "stakeholders": [
    {
      "id": "adult_tech_consumer_30_45",
      "title": "Adult Tech Consumer (30-45)",
      "profile": "I am a working adult between 30 and 45 years old...",
      "age_demography": "30-45 years old, urban and suburban, full-time working professionals",
      "tech_savviness": "Medium to high. Comfortable trying new apps but expects intuitive UX.",
      "product_context": "Consumer subscription and productivity apps"
    }
  ]
}
```

## 2. Start Simulation

Creates a simulation run and starts processing in the background.

### POST `/api/simulations`

Request body:

```json
{
  "user_input": "Our trial-to-paid conversion dropped after onboarding changes.",
  "stakeholder_id": "small_business_owner_35_55",
  "max_interview_messages": 8
}
```

Request rules:
- `user_input` is required.
- Provide either `stakeholder_id` OR `stakeholder_profile`.
- `stakeholder_profile` overrides `stakeholder_id` when both are sent.
- `max_interview_messages` range: `2` to `40`.

Alternative request with ad-hoc profile:

```json
{
  "user_input": "Users stop using our app after week 1.",
  "stakeholder_profile": "I am a 40 year old operations manager at a logistics company...",
  "max_interview_messages": 10
}
```

Response `202`:

```json
{
  "simulation_id": "f3fd70c7-f65f-4f76-85ac-b4ccfc8637f7",
  "status": "running",
  "events_url": "/api/simulations/f3fd70c7-f65f-4f76-85ac-b4ccfc8637f7/events",
  "details_url": "/api/simulations/f3fd70c7-f65f-4f76-85ac-b4ccfc8637f7"
}
```

Error responses:
- `400`: missing stakeholder input
- `404`: unknown `stakeholder_id`

## 3. Simulation Status

Poll this endpoint for status or final answer.

### GET `/api/simulations/{simulation_id}`

Response `200`:

```json
{
  "simulation_id": "f3fd70c7-f65f-4f76-85ac-b4ccfc8637f7",
  "status": "completed",
  "started_at": 1768859330.315928,
  "completed_at": 1768859335.749132,
  "error": null,
  "final_answer": "Conversion drop appears linked to onboarding complexity..."
}
```

Statuses:
- `running`
- `completed`
- `failed`

## 4. Live Stream (SSE)

Use SSE to render the interview process live in frontend.

### GET `/api/simulations/{simulation_id}/events`

Headers:
- `Accept: text/event-stream`

Event format:

```text
id: 0
event: simulation.step
data: {"event":"simulation.step","timestamp":1768859331.1,"simulation_id":"...","payload":{...}}
```

### Event Types

`sse.connected`
- Sent when stream is established.
- Payload:

```json
{ "status": "running" }
```

`simulation.started`
- Sent once, right after background run starts.
- Payload:

```json
{
  "max_interview_messages": 8,
  "user_input": "Our trial-to-paid conversion dropped..."
}
```

`simulation.step`
- Sent on each graph state update.
- Payload:

```json
{
  "step": 3,
  "state": {
    "hypothesis_offset": 0,
    "current_question": "Tell me about the last time this happened.",
    "final_answer": "",
    "hypotheses": [
      {
        "id": "h-1",
        "title": "Onboarding complexity hurt conversion",
        "status": "in_progress",
        "root_cause": "",
        "evidence_count": 0,
        "interview_message_count": 1
      }
    ]
  }
}
```

`agent.update`
- Primary timeline event for end-user progress rendering.
- Use this to show clear states like analysis, hypothesis generation, validation, interview, synthesis.
- Payload:

```json
{
  "step": 4,
  "stage": "interview",
  "action": "response_captured",
  "summary": "Captured stakeholder response for h-1.",
  "hypothesis_id": "h-1",
  "hypothesis_title": "Onboarding complexity hurt conversion",
  "details": {
    "message_index": 1,
    "content": "A/B test showed +12% conversion after simplification."
  }
}
```

`hypothesis.batch_created`
- Sent once after distillation, includes all generated hypotheses.
- Payload:

```json
{
  "step": 1,
  "count": 2,
  "hypotheses": [
    {
      "hypothesis_id": "h-1",
      "hypothesis_title": "Onboarding complexity hurt conversion",
      "hypothesis_description": "Simpler onboarding will improve trial-to-paid conversion."
    }
  ]
}
```

`interview.message`
- Sent for every newly created interview message.
- Payload:

```json
{
  "step": 4,
  "hypothesis_id": "h-1",
  "message_index": 1,
  "role": "user",
  "content": "A/B test showed +12% conversion after simplification.",
  "status": "in_progress"
}
```

`interview.transcript.updated`
- Sent after each new interview message with the latest full transcript for that hypothesis.
- Payload:

```json
{
  "step": 5,
  "hypothesis_id": "h-1",
  "hypothesis_title": "Onboarding complexity hurt conversion",
  "status": "in_progress",
  "latest_message_index": 1,
  "latest_message_role": "user",
  "latest_message_content": "A/B test showed +12% conversion after simplification.",
  "transcript": {
    "message_count": 2,
    "text": "assistant: What evidence shows onboarding complexity changed conversion?\nuser: A/B test showed +12% conversion after simplification.",
    "messages": [
      { "role": "assistant", "content": "What evidence shows onboarding complexity changed conversion?" },
      { "role": "user", "content": "A/B test showed +12% conversion after simplification." }
    ]
  }
}
```

`simulation.completed`
- Terminal event for successful run.
- Payload includes final state and final answer.

`simulation.error`
- Terminal event for failed run.
- Payload:

```json
{
  "message": "error text"
}
```

## Frontend Integration Flow

1. Call `GET /api/stakeholders` to populate stakeholder options.
2. On submit, call `POST /api/simulations`.
3. Open `EventSource` on returned `events_url`.
4. Render:
   - `agent.update` as the main activity timeline
   - `interview.message` or `interview.transcript.updated` as chat/transcript UI
   - `simulation.step` only for debug state (optional in UI)
5. On `simulation.completed`:
   - stop loading state
   - show `final_answer`
6. On `simulation.error`:
   - show error and retry action

## Minimal Frontend Example

```js
const createRun = async () => {
  const res = await fetch("/api/simulations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_input: "Our activation is dropping after signup redesign.",
      stakeholder_id: "adult_tech_consumer_30_45",
      max_interview_messages: 8
    })
  });
  const run = await res.json();
  const es = new EventSource(run.events_url);

  es.addEventListener("interview.message", (evt) => {
    const message = JSON.parse(evt.data);
    console.log("message", message.payload);
  });

  es.addEventListener("simulation.completed", (evt) => {
    const done = JSON.parse(evt.data);
    console.log("final", done.payload.final_answer);
    es.close();
  });

  es.addEventListener("simulation.error", (evt) => {
    const err = JSON.parse(evt.data);
    console.error(err.payload.message);
    es.close();
  });
};
```
