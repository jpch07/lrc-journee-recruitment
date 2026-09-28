# Render hostname-transfer request

To: support@render.com

Send from: jeanpaul07.chaaya@gmail.com (the account owning both services)

Subject: Move evalday.onrender.com to my existing Oregon service

Hello Render Support,

I want to move my application's existing Render hostname to an already-tested
service in another region, within the same workspace:

- Current: evalday, Virginia, srv-da61170u01pc738qvcag,
  https://evalday.onrender.com
- Destination: evalday-oregon, Oregon, srv-dasn8re0tbcc738644sg,
  https://evalday-oregon.onrender.com

Both services use the same external PostgreSQL database. Oregon performs better
for this application. I understand regions cannot be changed on an existing
service, and changing a service's display name did not change its onrender.com
hostname.

Can you reassign evalday.onrender.com directly to the existing Oregon service,
without a proxy, deleting either service, changing the database, or upgrading
either Free service? Please confirm feasibility, any interruption, the rollback
procedure, and that there is no additional charge before making changes.

If reassignment is not possible, can you change the Oregon service's hostname
to lrcevalday.onrender.com, if available, without recreating the service?

The application contains important recruitment records, so please keep both
services and their configuration intact until we agree on the procedure.

Thank you,
Jean-Paul Chaaya

---
Draft only; not sent. Recipient verified in Render's official documentation:
https://render.com/docs/migrate-from-railway
