"""Shared client-ledger (udhaar) command handling - text in, reply text out.

Used by both the WhatsApp webhook (src/handlers/webhook.py) and the public
web demo (src/handlers/web_demo.py), so the AI disambiguation/confirmation
flow only has one implementation to stay correct. Delivery-agnostic: it
never sends anything itself, it just returns the reply text for the caller
to deliver (via WhatsApp or an HTTP JSON response).
"""

from typing import Optional

from src.services.ai_extractor import GeminiExtractor
from src.services.database import DynamoDBClient

LEDGER_KEYWORDS = (
    "owe", "owes", "owed", "udhaar", "udhar", "paid", "payment", "balance",
    "baaki", "due", "cleared", "settled", "credit",
)


class LedgerAssistant:
    """Resolves a text message into a ledger action (add entry/mark paid/
    check balance), or a reply continuing an earlier ambiguous-name
    confirmation. identifier is whatever partitions the ledger for this
    caller - a WhatsApp phone_number, or a demo session key."""

    def __init__(self, db: DynamoDBClient, extractor: GeminiExtractor, ttl_days: Optional[int] = None):
        self.db = db
        self.extractor = extractor
        self.ttl_days = ttl_days  # None = real data, never expires. Set for throwaway demo data.

    def looks_like_ledger_command(self, text: str) -> bool:
        """Cheap keyword pre-filter so a normal quotation message (the common
        case on WhatsApp) never pays for an extra AI classification call."""
        lower = text.lower()
        return any(kw in lower for kw in LEDGER_KEYWORDS)

    def handle(self, identifier: str, text: str, force: bool = False) -> Optional[str]:
        """Returns the reply text, or None if this doesn't look like a ledger
        command and force=False (so the caller can fall through to something
        else, e.g. quotation generation). force=True always returns a reply
        (used by the dedicated ledger demo, where every input is a ledger
        command by definition)."""
        pending = self.db.get_pending_ledger_action(identifier)
        if pending:
            return self._resolve_pending(identifier, text, pending)

        if not force and not self.looks_like_ledger_command(text):
            return None

        known_clients = [b["client_name"] for b in self.db.get_ledger_balances(identifier)]
        command = self.extractor.classify_text_command(text, known_clients=known_clients)
        return self._dispatch(identifier, command, force)

    def handle_audio(self, identifier: str, audio_bytes: bytes, audio_mime_type: str) -> str:
        """Same as handle(), but for a recorded voice note instead of text -
        always force-mode (used by the dedicated ledger demo's mic input,
        where every recording is a ledger command by definition). Works in
        whatever language the person spoke, same as the WhatsApp bot."""
        pending = self.db.get_pending_ledger_action(identifier)
        if pending:
            transcript = self.extractor.transcribe_audio(audio_bytes, audio_mime_type) or ""
            return self._resolve_pending(identifier, transcript, pending)

        known_clients = [b["client_name"] for b in self.db.get_ledger_balances(identifier)]
        command = self.extractor.classify_voice_command(audio_bytes, audio_mime_type, known_clients=known_clients)
        return self._dispatch(identifier, command, force=True)

    def _dispatch(self, identifier: str, command, force: bool) -> Optional[str]:
        if not command or command.action not in ("add_ledger_entry", "mark_ledger_paid", "check_balance"):
            if force:
                return (
                    "I didn't catch a ledger command there. Try something like "
                    "'Ramesh owes me 2000 for tiling work', 'who owes me money', "
                    "or 'mark Ramesh as paid'."
                )
            return None

        candidates = list(dict.fromkeys(command.ambiguous_candidates or []))

        if command.action == "add_ledger_entry":
            if not command.client_name or not command.amount:
                return "I couldn't tell who owes how much. Try something like 'Ramesh owes me 2000 for tiling work'."
            if len(candidates) >= 2:
                self.db.set_pending_ledger_action(
                    identifier,
                    {
                        "action": "add_ledger_entry",
                        "candidates": candidates,
                        "amount": command.amount,
                        "description": command.ledger_description,
                        "due_date": command.due_date,
                    },
                )
                return f"Did you mean {self.format_candidates(candidates)}, or someone new? Reply with the name to confirm."
            return self._add_ledger_entry_and_reply(
                identifier, command.client_name, command.amount, command.ledger_description, command.due_date
            )

        if command.action == "mark_ledger_paid":
            if not command.client_name_query:
                return "Who should I mark as paid?"
            if len(candidates) >= 2:
                self.db.set_pending_ledger_action(
                    identifier, {"action": "mark_ledger_paid", "candidates": candidates}
                )
                return f"Did you mean {self.format_candidates(candidates)}? Reply with the name to confirm."
            return self._mark_ledger_paid_and_reply(identifier, command.client_name_query)

        # check_balance
        balances = self.db.get_ledger_balances(identifier, client_name_query=command.client_name_query)
        if not balances:
            target = command.client_name_query or "Everyone"
            return f"No pending balance for {target}." if command.client_name_query else "No pending balances right now."
        if command.client_name_query:
            exact = next(
                (b for b in balances if b["client_name"].lower() == command.client_name_query.strip().lower()), None
            )
            if exact:
                return f"{exact['client_name']} owes you ₹{exact['total']:,.0f} ({exact['entry_count']} entries)."
            if len(balances) == 1:
                b = balances[0]
                return f"{b['client_name']} owes you ₹{b['total']:,.0f} ({b['entry_count']} entries)."
            lines = [f"{b['client_name']}: ₹{b['total']:,.0f}" for b in balances]
            return f"A few names match '{command.client_name_query}':\n" + "\n".join(lines)

        lines = [f"{b['client_name']}: ₹{b['total']:,.0f}" for b in balances]
        grand_total = sum(b["total"] for b in balances)
        return "Who owes you money:\n" + "\n".join(lines) + f"\n\nTotal: ₹{grand_total:,.0f}"

    def format_candidates(self, candidates: list[str]) -> str:
        if len(candidates) == 2:
            return f"{candidates[0]} or {candidates[1]}"
        return ", ".join(candidates[:-1]) + f", or {candidates[-1]}"

    def _add_ledger_entry_and_reply(
        self, identifier: str, client_name: str, amount: float, description: Optional[str], due_date: Optional[str]
    ) -> str:
        self.db.create_ledger_entry(
            identifier,
            client_name=client_name,
            amount=amount,
            description=description,
            due_date=due_date,
            ttl_days=self.ttl_days,
        )
        balances = self.db.get_ledger_balances(identifier, client_name_query=client_name)
        own_bucket = next((b for b in balances if b["client_name"].lower() == client_name.lower()), None)
        total = own_bucket["total"] if own_bucket else amount
        entry_count = own_bucket["entry_count"] if own_bucket else 1
        return f"Added: {client_name} owes ₹{total:,.0f}" + (
            f" (total across {entry_count} entries)" if entry_count > 1 else ""
        )

    def _mark_ledger_paid_and_reply(self, identifier: str, client_name: str) -> str:
        paid_entries = self.db.mark_ledger_entries_paid(identifier, client_name)
        if not paid_entries:
            return f"No pending balance found for {client_name}."
        total_cleared = sum(e["amount"] for e in paid_entries)
        return f"Marked {paid_entries[0]['client_name']} as paid — cleared ₹{total_cleared:,.0f}."

    def _resolve_pending(self, identifier: str, text: str, pending: dict) -> str:
        """Handle a reply to an earlier ambiguous-name confirmation question."""
        reply_lower = text.strip().lower()
        candidates = pending.get("candidates", [])

        if reply_lower in ("cancel", "no", "neither", "none"):
            self.db.clear_pending_ledger_action(identifier)
            return "Cancelled."

        resolved = next((c for c in candidates if c.lower() == reply_lower), None)
        if not resolved:
            resolved = next((c for c in candidates if reply_lower in c.lower() or c.lower() in reply_lower), None)
        if not resolved:
            # Not a recognizable reply to the candidates - treat it as the name
            # of a genuinely new/different client instead of giving up.
            resolved = text.strip()

        self.db.clear_pending_ledger_action(identifier)

        if pending.get("action") == "add_ledger_entry":
            return self._add_ledger_entry_and_reply(
                identifier, resolved, pending.get("amount"), pending.get("description"), pending.get("due_date")
            )
        if pending.get("action") == "mark_ledger_paid":
            return self._mark_ledger_paid_and_reply(identifier, resolved)
        return "Something went wrong with that confirmation. Please try again."
