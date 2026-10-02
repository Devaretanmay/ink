# Data Handling

## Data Stored
Microloop stores the following strictly for internal operation and evaluation:
- **Decision States:** Contextual data fed into the decision process.
- **Choices:** The set of available bounded choices at the decision site.
- **Outcomes:** The recorded outcome data used to qualify decisions.
- **Artifact Payloads:** Serialized decision logic and bounds.
- **Coverage Maps:** Telemetry on which paths are observed, shadowed, and active.

## Storage Location
All data is stored locally in an embedded SQLite WAL database.

## Data Egress
By default, **no data leaves the machine**. All evaluation, compilation, and qualification occur locally. 

## Data Retention and Deletion
Data retention policies are controlled locally. Deleting the `.microloop/` directory removes all stored states, traces, and artifacts. There is no remote backup managed by Microloop.

## GDPR Considerations
Since all data remains on the host machine and is fully controlled by the user's infrastructure, GDPR compliance falls within the application's existing data handling boundaries. Microloop introduces no third-party data processors.
