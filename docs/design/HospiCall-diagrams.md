# HospiCall — System Diagrams

> All diagrams in Mermaid — render via mermaid.live, VS Code Mermaid extension, or GitHub Markdown.

---

## 1. ER Diagram

```mermaid
erDiagram
    USER ||--o{ APPOINTMENT : books
    USER ||--o{ CONVERSATION : has
    USER ||--o{ USER_PREFERENCE : has
    DOCTOR ||--o{ DOCTOR_SCHEDULE : has
    DOCTOR ||--o{ APPOINTMENT : serves
    CONVERSATION ||--o{ TRANSCRIPT_SEGMENT : contains
    CONVERSATION ||--o{ INTENT : produces
    CONVERSATION ||--o{ SENTIMENT : scored
    CONVERSATION ||--o{ ACTION : performs
    APPOINTMENT ||--o{ APPOINTMENT_CHANGE_LOG : "tracks"
    APPOINTMENT ||--o{ REMINDER : schedules

    USER {
        bigint id PK
        varchar phone UK
        varchar name
        date dob
        varchar address
        varchar insurance_id
        timestamp created_at
    }
    USER_PREFERENCE {
        bigint id PK
        bigint user_id FK
        varchar preferred_doctor
        varchar language
        varchar reminder_channel
        boolean consent_recorded
    }
    CONVERSATION {
        uuid id PK
        bigint user_id FK
        varchar call_id UK
        timestamp started_at
        timestamp ended_at
        varchar outcome
    }
    TRANSCRIPT_SEGMENT {
        bigint id PK
        uuid conv_id FK
        varchar speaker
        text text
        float confidence
        timestamp ts
    }
    INTENT {
        bigint id PK
        uuid conv_id FK
        varchar intent_type
        jsonb extracted_slots
        float confidence
    }
    SENTIMENT {
        bigint id PK
        uuid conv_id FK
        bigint segment_id FK
        varchar label
        float score
    }
    ACTION {
        bigint id PK
        uuid conv_id FK
        varchar action_type
        jsonb payload
    }
    DOCTOR {
        bigint id PK
        varchar name
        varchar specialization
        varchar location
        varchar contact
        float rating
    }
    DOCTOR_SCHEDULE {
        bigint id PK
        bigint doctor_id FK
        date date
        time start_time
        time end_time
        int slot_duration_min
        varchar status
    }
    APPOINTMENT {
        bigint id PK
        bigint user_id FK
        bigint doctor_id FK
        timestamp slot_start
        timestamp slot_end
        varchar status
        timestamp booked_at
    }
    APPOINTMENT_CHANGE_LOG {
        bigint id PK
        bigint appointment_id FK
        jsonb old_values
        jsonb new_values
        timestamp changed_at
    }
    REMINDER {
        bigint id PK
        bigint appointment_id FK
        timestamp scheduled_for
        timestamp sent_at
        varchar channel
    }
```

---

## 2. Sequence Diagram — Happy Path Booking

```mermaid
sequenceDiagram
    participant P as Patient
    participant FS as FreeSWITCH
    participant SV as Session Manager
    participant STT as Whisper (OmniVoice)
    participant OR as Orchestrator (FastAPI)
    participant LLM as Llama 3.1 8B (Ollama)
    participant DB as Database
    participant TTS as TTS (OmniVoice)
    participant NTF as Notification Svc

    P->>FS: dials 9441321662
    FS->>SV: SIP INVITE + caller ID
    SV->>DB: lookup user by phone
    DB-->>SV: user profile (or null)
    SV-->>FS: call accepted
    FS->>TTS: greet user
    TTS-->>FS: audio stream
    FS-->>P: "Welcome to City Hospital..."
    P->>FS: "I want a cardiologist tomorrow"
    FS->>STT: stream audio
    STT-->>OR: text: "I want a cardiologist tomorrow"
    OR->>LLM: classify intent + extract slots
    LLM-->>OR: intent=book, dept=cardiology, when=tomorrow
    OR->>DB: query cardiologists + availability
    DB-->>OR: 2 doctors with free slots
    OR->>LLM: generate offer options
    LLM-->>OR: "Dr Mehta 10am, Dr Rao 2pm. Which?"
    OR->>TTS: synthesize offer
    TTS-->>FS: audio
    FS-->>P: plays offer
    P->>FS: "10am with Dr Mehta"
    FS->>STT: stream audio
    STT-->>OR: text
    OR->>DB: check slot free + reserve atomically
    DB-->>OR: confirmed (row lock)
    OR->>LLM: generate confirmation
    LLM-->>OR: "Booked for tomorrow 10am..."
    OR->>TTS: synthesize confirmation
    TTS-->>FS: audio
    FS-->>P: plays confirmation
    OR->>NTF: send SMS + email
    OR->>DB: log transcript, intent, action
```

---

## 3. Sequence Diagram — User Interrupts AI

```mermaid
sequenceDiagram
    participant P as Patient
    participant FS as FreeSWITCH
    participant VAD as VAD
    participant TTS as TTS
    participant OR as Orchestrator
    participant LLM as Llama 3.1 8B

    P->>FS: speaks while AI is talking
    FS->>VAD: new inbound audio energy detected
    VAD-->>FS: speech started (barge-in)
    FS->>TTS: ABORT current audio chunk
    TTS-->>FS: stops immediately
    FS-->>P: silence (AI stopped)
    FS->>OR: forward new user audio
    OR->>LLM: process new input with existing context
    LLM-->>OR: response text
    OR->>TTS: synthesize new response
    TTS-->>FS: audio
    FS-->>P: new response plays
```

---

## 4. Sequence Diagram — Failure / Escalation

```mermaid
sequenceDiagram
    participant P as Patient
    participant FS as FreeSWITCH
    participant STT as Whisper
    participant OR as Orchestrator
    participant LLM as Llama 3.1 8B
    participant RE as Rule Engine
    participant LOG as Logging

    P->>FS: "I'm having chest pain"
    FS->>STT: stream audio
    STT-->>OR: text: "I'm having chest pain"
    OR->>LLM: classify intent
    LLM-->>OR: intent=medical_emergency
    OR->>RE: check emergency rules
    RE-->>OR: ESCALATE
    OR->>LLM: generate emergency script
    LLM-->>OR: "Call 108 immediately..."
    OR->>FS: play emergency instructions + hospital ER number
    FS-->>P: plays script
    OR->>LOG: log emergency event, alert admin
    OR->>DB: record escalated action

    Note over OR,LLM: If LLM call times out
    OR->>FS: fallback recorded message (never silent)
    OR->>LOG: log LLM timeout
```

---

## 5. Deployment Diagram — Demo (Laptop)

```mermaid
flowchart LR
    subgraph P[Patient]
        PHONE[Landline / Mobile]
    end
    subgraph PSTN[PSTN Network]
        FWD[Call Forwarding on 9441321662]
    end
    subgraph LAPTOP[Laptop - i9-13900H, 16GB RAM]
        subgraph TEL[Telephony Layer]
            FSW[FreeSWITCH :5060]
        end
        subgraph APP[Application Layer]
            API[FastAPI Backend :8000]
            SESS[Session Manager]
        end
        subgraph AI[AI Layer]
            OMN[OmniVoice-Studio :3900]
            OLL[Ollama / Llama 3.1 8B :11434]
        end
        subgraph DATA[Data Layer]
            SQL[SQLite file]
            RED[Redis :6379]
            GRA[Prometheus + Grafana :9090/:3000]
        end
        subgraph FE[Frontend]
            DASH[Admin Dashboard :5173]
        end
    end

    PHONE <--> PSTN
    FWD <--> FSW
    FSW <--> API
    API <--> SESS
    API <--> OMN
    API <--> OLL
    API <--> SQL
    API <--> RED
    API <--> DASH
    OMN -.STT/TTS.-> API
    OLL -.LLM.-> API
    API --> GRA
```

---

## 6. Data Flow Diagram — Booking Request (field-level)

```mermaid
flowchart TD
    A["Caller ID phone='9441321662'"] --> B[Session created]
    B --> C{"Known user?"}
    C -- Yes --> D["USER.name, USER_PREFERENCE.preferred_doctor"]
    C -- No --> E["anonymous session, greet generically"]
    D --> F[STT text]
    E --> F
    F --> G{Intent classify}
    G -- book --> H["slots: department='cardiology', when='tomorrow'"]
    H --> I[Query DOCTOR where specialization='cardiology']
    I --> J[Query DOCTOR_SCHEDULE for date + free slots]
    J --> K[Rank by slot proximity]
    K --> L["Offer string to LLM"]
    L --> M[TTS speech]
    M --> N[Patient picks slot]
    N --> O[APPOINTMENT insert: user_id, doctor_id, slot_start, slot_end, status='booked']
    O --> P[APPOINTMENT_CHANGE_LOG insert]
    P --> Q[REMINDER insert + Notification Svc]
    Q --> R[CONVERSATION / TRANSCRIPT_SEGMENT / INTENT / ACTION inserts]
```

---

## 7. Call-Latency Budget Diagram

```mermaid
flowchart LR
    subgraph Streaming
        A["VAD detect"] --> B["STT streaming 400ms"]
        B --> C["Intent 100ms"]
        C --> D["LLM 600ms"]
        D --> E["DB lookup 100ms"]
        E --> F["TTS stream 400ms"]
    end
    A -.parallel.-> F
    B -.parallel.-> F
    F --> G["First audio heard ~1.6s"]
```
