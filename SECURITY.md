# Vantav Security Policy

**Effective date:** August 14, 2026  
**Contact:** security@vantavcommerce.com

This policy describes the security controls Vantav LLC uses to protect the Vantav platform, merchant data, and connected sales channels.

## 1. Security Program

Vantav LLC maintains a security program that covers:

- Secure software development and code review.
- Role-based access control for production systems.
- Regular dependency and vulnerability review.
- Incident monitoring through Sentry and application logs.
- Periodic review of access, secrets, and vendor integrations.

## 2. Infrastructure & Network Security

- The application is hosted on Render and AWS cloud infrastructure.
- Network traffic is encrypted with TLS/HTTPS.
- Internal services and databases are not exposed to the public internet.
- Each merchant’s data is isolated at the application layer and enforced by PostgreSQL Row-Level Security (RLS).
- Rate limiting and request throttling protect public endpoints.

## 3. Application Security

- All merchant credentials are hashed using `pbkdf2:sha256`.
- Site-wall and session endpoints use cryptographically random tokens and `HttpOnly`, `Secure`, `SameSite=Lax` cookies.
- Webhook signatures are verified with HMAC-SHA256 before processing.
- A Content Security Policy (CSP) mitigates XSS and unauthorized script execution.
- SQL queries are parameterized through SQLAlchemy to prevent injection.
- Account tiers and role checks restrict access to admin and engineer endpoints.

## 4. Data Protection

- Data is encrypted in transit via TLS.
- Database connections use authenticated, encrypted transport.
- Merchant channel credentials are stored as secrets and not logged.
- Data retention follows the published Privacy Policy; merchants may request deletion.

## 5. Endpoint Security

- Company endpoints use modern operating systems with built-in anti-malware/endpoint protection.
- Automatic security updates are enabled.
- Access to production systems is restricted to authorized personnel.
- Secrets and API keys are managed through a secret store, not committed to source control.

## 6. Vulnerability Disclosure

If you believe you have found a security issue, please email **security@vantavcommerce.com** with a description and reproduction steps. We will investigate and respond as quickly as possible.

## 7. Changes to This Policy

We may update this policy as our security practices evolve. The latest version is always available at https://vantavcommerce.com/security.
