# Vantav Security and Privacy Policy

**Effective date:** August 14, 2026  
**Contact:** security@vantavcommerce.com  
**Published at:** https://vantavcommerce.com/security

This policy describes the security, privacy, and data protection controls Vantav LLC uses to protect the Vantav platform, merchant data, and connected sales channels.

## 1. Security Program

Vantav LLC maintains a security program that covers:

- Secure software development and code review.
- Role-based access control for production systems.
- Regular dependency and vulnerability review.
- Incident monitoring through Sentry and application logs.
- Periodic review of access, secrets, and vendor integrations.

## 2. Security Baseline for Daily Operations

Company personnel follow a security baseline that includes:

- Automatic screen locking and strong device passwords or biometrics.
- Multi-factor authentication (MFA) for critical services and production accounts.
- Unique, complex passwords managed through a password manager.
- Clear-desk and clear-screen practices.
- Approved, up-to-date devices only; automatic security updates enabled.

## 3. Access Control and Least Privilege

Access to Vantav systems and data is governed by the principle of least privilege:

- Production access is restricted to authorized personnel with a business need.
- Roles and permissions are tiered (merchant, admin, engineer) and enforced in code.
- Database access is scoped per merchant through PostgreSQL Row-Level Security (RLS).
- Secrets, API keys, and credentials are stored in a secret manager and never committed to source control.

## 4. Data Classification and Encryption

Vantav classifies data by sensitivity:

- **Sensitive**: merchant credentials, payment and channel API tokens, personal data.
- **Confidential**: business metrics, order history, customer lists.
- **Public**: published policies and marketing content.

Controls:

- All web traffic and API calls are encrypted in transit using TLS.
- Database connections use authenticated, encrypted transport.
- Sensitive credentials are stored as secrets and not logged.
- Data at rest is encrypted by the managed database and cloud storage providers used by Render/AWS.
- Data retention follows the published Privacy Policy; merchants may request deletion.

## 5. Incident Response

Vantav has an incident response process with defined roles and communication channels:

- Incidents are detected through automated monitoring (Sentry, application logs, health checks) and manual reports.
- Response is coordinated by the Vantav engineering/security lead.
- Affected merchants or partners are notified promptly when required by law or contract.
- Post-incident reviews are conducted and controls are updated as needed.
- Report incidents to **security@vantavcommerce.com**.

## 6. Vulnerability and Threat Management

Vantav maintains a vulnerability management program:

- Dependencies are reviewed regularly and updated when security patches are available.
- Webhooks and API endpoints use HMAC signature verification and rate limiting.
- Security scans and code review are part of the deployment workflow.
- Third-party reports are handled through the vulnerability disclosure process.

## 7. Infrastructure & Network Security

- The application is hosted on Render and AWS cloud infrastructure.
- Network traffic is encrypted with TLS/HTTPS.
- Internal services and databases are not exposed to the public internet.
- Each merchant’s data is isolated at the application layer and enforced by PostgreSQL Row-Level Security (RLS).
- Rate limiting and request throttling protect public endpoints.

## 8. Application Security

- All merchant credentials are hashed using `pbkdf2:sha256`.
- Site-wall and session endpoints use cryptographically random tokens and `HttpOnly`, `Secure`, `SameSite=Lax` cookies.
- Webhook signatures are verified with HMAC-SHA256 before processing.
- A Content Security Policy (CSP) mitigates XSS and unauthorized script execution.
- SQL queries are parameterized through SQLAlchemy to prevent injection.
- Account tiers and role checks restrict access to admin and engineer endpoints.

## 9. Data Protection and Privacy

- Vantav maintains an internal personal data protection policy and a published Privacy Policy at https://vantavcommerce.com/privacy.
- Both policies are reviewed and updated as the platform and regulations evolve.
- Merchants and users can request access, correction, deletion, or portability of their data by contacting **support@vantavcommerce.com**.
- At the end of a contractual relationship, Vantav deletes or anonymizes collected customer data upon request, except where retention is required by law.

## 10. Breach Notification

Vantav has a notification process to alert affected merchants and relevant authorities of suspected or confirmed data breaches in accordance with applicable law and contractual obligations.

## 11. Endpoint Security

- Company endpoints use modern operating systems with built-in anti-malware/endpoint protection.
- Automatic security updates are enabled.
- Access to production systems is restricted to authorized personnel.
- Secrets and API keys are managed through a secret store, not committed to source control.

## 12. Vulnerability Disclosure

If you believe you have found a security issue, please email **security@vantavcommerce.com** with a description and reproduction steps. We will investigate and respond as quickly as possible.

## 13. Changes to This Policy

We may update this policy as our security practices evolve. The latest version is always available at https://vantavcommerce.com/security.
