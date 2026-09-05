using System;
using System.Collections.Generic;
using System.Linq;
using WsjtxUdpLib.Messages.Out;

namespace WSJTX_Controller
{
    // Simple Autoreply -- an opt-in operating mode that replaces Jimmy's full
    // classification/Call-Filters admission stack with one small filter set whose
    // defaults admit everything.
    //
    // Rationale: the normal admission path (WsjtxClient.CallQueue.AddSelectedCall)
    // is built around award/category work -- new DXCC, WAS, directed CQ, rank order.
    // That is exactly right for award chasing and exactly wrong for an operator who
    // just wants "call CQ and work whoever answers". Rather than bolting more
    // exceptions onto that stack, this mode short-circuits it: when Enabled is true
    // the caller consults Accepts() instead of the category switch.
    //
    // Every filter here is off by default, so turning the mode on with nothing else
    // configured means "reply to everyone" -- including stations already worked on
    // this band, which the normal path rejects.
    //
    // Deliberately UI-free and free of WsjtxClient state so it is directly unit
    // testable (see AutoReplyFilterTests in JimmyTests.cs). Gates that are safety
    // rather than preference -- the blocked-call list, the already-in-queue check,
    // the T/R period check, the per-period queue cap -- stay in the caller and are
    // never bypassed by this mode.
    public class AutoReplyFilter
    {
        public enum ListModes
        {
            ALLOW,      //list is exclusive: only listed stations are replied to
            EXCLUDE     //list is a denylist: listed stations are skipped
        }

        public enum DxccScopes
        {
            ANY_BAND,           //entity never worked on any band
            CURRENT_BAND,       //entity not yet worked on the band in use
            NEW_OR_UNCONFIRMED  //entity not yet CONFIRMED: never worked, or worked
                                //but with no LoTW/QRZ confirmation on file
        }

        // Master switch. False -> Jimmy behaves exactly as it does on master; no
        // caller consults this object's filters at all.
        public bool Enabled { get; set; } = false;

        // Reply to stations that answer our own CQ. On by default within the mode --
        // it is the whole point of "I call CQ and Jimmy works the answers".
        public bool ReplyToMyCallers { get; set; } = true;

        // Also reply to stations calling CQ themselves. Off by default: with this off
        // the mode only ever works people who called us.
        public bool ReplyToCqCallers { get; set; } = false;

        // Weak-signal floor for this mode only. Off by default; when off, the normal
        // Receive-tab SNR floor is not applied either (this mode owns the decision).
        public bool MinSnrEnabled { get; set; } = false;
        public int MinSnr { get; set; } = -24;

        // Skip stations already worked on this band. Off by default -- "reply to
        // everyone" includes repeats.
        public bool NewCallsOnly { get; set; } = false;

        // Skip directed CQs that do not include us (e.g. "CQ JA" heard in Europe).
        // On by default: answering those is bad operating practice, not a preference.
        public bool ExcludeUnmatchedDirectedCq { get; set; } = true;

        // Work only DXCC entities still missing from the log. Off by default.
        //
        // The two flags this reads arrive from WSJT-X in the decode itself
        // (DecodeMessage.Parse, IsNewCountry / IsNewCountryOnBand) and are computed
        // against WSJT-X's own log, so this needs no country lookup of its own and
        // agrees with what WSJT-X shows the operator.
        //
        // Deliberately not applied to stations answering OUR CQ: turning somebody away
        // mid-call because their entity is already in the log would be rude on the air
        // and would waste a QSO that is already half made.
        public bool NewDxccOnly { get; set; } = false;
        public DxccScopes NewDxccScope { get; set; } = DxccScopes.ANY_BAND;

        // True only when the caller actually has to work out whether the entity is
        // worked-but-unconfirmed. That answer costs a callsign lookup, so the caller
        // checks this first rather than paying for it on every decode.
        public bool NeedsUnconfirmedDxccFlag
        {
            get { return NewDxccOnly && NewDxccScope == DxccScopes.NEW_OR_UNCONFIRMED; }
        }

        public ListModes ListMode { get; set; } = ListModes.ALLOW;

        // Free-form station list. Empty (the default) means the list is not applied
        // at all, in either mode. Each token matches a decode if it equals the
        // station's continent code, equals its country name, or is a prefix of its
        // callsign -- so "EU", "Spain" and "EA" are all valid tokens.
        public List<string> ListTokens { get; } = new List<string>();

        // Does this decode show the station is free to be called?
        //
        // Only two kinds do: a CQ (it is calling right now) and a 73/RR73 (it has just
        // finished and is about to be free). Everything else exchanged between two other
        // stations -- a signal report, a grid reply, a bare RRR -- proves the opposite:
        // the station is mid-QSO with somebody else, and calling it wastes both operators'
        // time. RRR is deliberately excluded (Is73orRR73 already does): per protocol it
        // just means "all received", not a sign-off, so a station can keep repeating it
        // while still working the other party.
        //
        // Only applies to the CQ-caller path. Stations answering US take the TO_MYCALL
        // branch, where a report or grid reply is exactly what we want to see.
        //
        // Regression coverage for 2026-09-05: relaxing the directed-CQ gate admitted all
        // third-party traffic, and the queue filled with stations busy working someone
        // else (e.g. "BG8HNC LY3BFH -16"). Jimmy dutifully called them and, of course,
        // none ever replied.
        public static bool ShowsStationAvailable(EnqueueDecodeMessage emsg)
        {
            if (emsg == null) return false;
            return emsg.IsCQ() || emsg.Is73orRR73();
        }

        // Returns true if this decode should be admitted. reason is a short
        // diagnostic tag for DebugOutput when the answer is false.
        // isAcceptableCq: caller-supplied -- false only for a directed CQ that does
        // not include us. Non-CQ decodes pass true.
        // applyNewDxccFilter: false on the TO_MYCALL path, where a station is already
        // calling us and must not be turned away over its entity (see NewDxccOnly).
        public bool Accepts(EnqueueDecodeMessage emsg, string deCall, bool isAcceptableCq,
                            bool applyNewDxccFilter, bool isDxccUnconfirmed, out string reason)
        {
            reason = null;
            if (emsg == null) { reason = "null decode"; return false; }

            if (ExcludeUnmatchedDirectedCq && !isAcceptableCq)
            {
                reason = "directed CQ not for us";
                return false;
            }

            if (applyNewDxccFilter && NewDxccOnly && !IsWantedDxcc(emsg, isDxccUnconfirmed))
            {
                switch (NewDxccScope)
                {
                    case DxccScopes.CURRENT_BAND:
                        reason = "DXCC already worked on this band"; break;
                    case DxccScopes.NEW_OR_UNCONFIRMED:
                        reason = "DXCC already confirmed"; break;
                    default:
                        reason = "DXCC already worked"; break;
                }
                return false;
            }

            if (RejectsSnr(emsg.Snr))
            {
                reason = $"snr {emsg.Snr} < {MinSnr}";
                return false;
            }

            if (NewCallsOnly && !emsg.IsNewCallOnBand)
            {
                reason = "already worked on band";
                return false;
            }

            if (ListTokens.Count > 0)
            {
                bool matched = MatchesList(emsg, deCall);
                if (ListMode == ListModes.ALLOW && !matched)
                {
                    reason = "not in allow list";
                    return false;
                }
                if (ListMode == ListModes.EXCLUDE && matched)
                {
                    reason = "in exclude list";
                    return false;
                }
            }

            return true;
        }

        // Split out so the TO_MYCALL path (WsjtxClient.ProcessDecodeMsg), which does its
        // weak-signal check before it has anything else to decide, applies exactly the
        // same floor as Accepts() rather than a second, subtly different one.
        // Note the comparison is strict: a decode exactly at MinSnr is admitted, unlike
        // the Receive-tab floor this replaces, which rejects at or below its value.
        public bool RejectsSnr(int snr)
        {
            return MinSnrEnabled && snr < MinSnr;
        }

        // ANY_BAND is the strictest: the entity must be missing from the log entirely.
        // CURRENT_BAND admits an entity already worked elsewhere but not yet on the band
        // in use, which is what band-slot chasing wants.
        // NEW_OR_UNCONFIRMED is about the award rather than the log: an entity counts
        // while it is still missing a confirmation, whether that is because it was never
        // worked or because it was worked and nobody ever confirmed it. Jimmy treats an
        // entity as confirmed only on a LoTW or QRZ QSL (LogbookDb.LoadHrcCache), so a
        // paper card or eQSL does not take a station off this list.
        //
        // isDxccUnconfirmed is supplied by the caller because answering it needs the
        // logbook and a callsign lookup, neither of which belongs in here.
        public bool IsWantedDxcc(EnqueueDecodeMessage emsg, bool isDxccUnconfirmed)
        {
            if (emsg == null) return false;
            switch (NewDxccScope)
            {
                case DxccScopes.CURRENT_BAND:
                    return emsg.IsNewCountryOnBand;
                case DxccScopes.NEW_OR_UNCONFIRMED:
                    // A never-worked entity is unconfirmed by definition, but it is not in
                    // the worked-minus-confirmed set, so both halves are needed here.
                    return emsg.IsNewCountry || isDxccUnconfirmed;
                default:
                    return emsg.IsNewCountry;
            }
        }

        public bool MatchesList(EnqueueDecodeMessage emsg, string deCall)
        {
            if (emsg == null) return false;
            foreach (string token in ListTokens)
            {
                if (string.IsNullOrEmpty(token)) continue;
                if (string.Equals(token, emsg.Continent, StringComparison.OrdinalIgnoreCase)) return true;
                if (string.Equals(token, emsg.Country, StringComparison.OrdinalIgnoreCase)) return true;
                if (deCall != null && deCall.StartsWith(token, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }

        // Parses the operator's free-text station list. Accepts commas, semicolons
        // and whitespace as separators so a pasted list works either way.
        public void SetListFromText(string text)
        {
            ListTokens.Clear();
            if (string.IsNullOrWhiteSpace(text)) return;
            foreach (string tok in text.Split(new[] { ',', ';', ' ', '\t', '\r', '\n' },
                                              StringSplitOptions.RemoveEmptyEntries))
            {
                string t = tok.Trim();
                if (t.Length > 0 && !ListTokens.Contains(t, StringComparer.OrdinalIgnoreCase))
                    ListTokens.Add(t);
            }
        }

        public string ListAsText()
        {
            return string.Join(", ", ListTokens);
        }

        // INI keys are all prefixed autoReplySimple* and every one defaults to the
        // pre-existing behaviour when absent, so an INI written by an older Jimmy
        // loads with the mode off and nothing else changed.
        public void LoadFromIni(IniFile ini)
        {
            if (ini == null) return;
            Enabled = ini.Read("autoReplySimpleEnabled") == "True";
            ReplyToMyCallers = ini.Read("autoReplySimpleMyCallers") != "False";
            ReplyToCqCallers = ini.Read("autoReplySimpleCqCallers") == "True";
            MinSnrEnabled = ini.Read("autoReplySimpleMinSnrEnabled") == "True";
            if (int.TryParse(ini.Read("autoReplySimpleMinSnr"), out int snr) && snr >= -30 && snr <= 30)
                MinSnr = snr;
            NewCallsOnly = ini.Read("autoReplySimpleNewOnly") == "True";
            NewDxccOnly = ini.Read("autoReplySimpleNewDxccOnly") == "True";
            string dxccScope = ini.Read("autoReplySimpleNewDxccScope");
            NewDxccScope = dxccScope == "CURRENT_BAND" ? DxccScopes.CURRENT_BAND
                         : dxccScope == "NEW_OR_UNCONFIRMED" ? DxccScopes.NEW_OR_UNCONFIRMED
                         : DxccScopes.ANY_BAND;
            ExcludeUnmatchedDirectedCq = ini.Read("autoReplySimpleExcludeDirCq") != "False";
            ListMode = ini.Read("autoReplySimpleListMode") == "EXCLUDE" ? ListModes.EXCLUDE : ListModes.ALLOW;
            SetListFromText(ini.Read("autoReplySimpleList"));
        }

        public void SaveToIni(IniFile ini)
        {
            if (ini == null) return;
            ini.Write("autoReplySimpleEnabled", Enabled.ToString());
            ini.Write("autoReplySimpleMyCallers", ReplyToMyCallers.ToString());
            ini.Write("autoReplySimpleCqCallers", ReplyToCqCallers.ToString());
            ini.Write("autoReplySimpleMinSnrEnabled", MinSnrEnabled.ToString());
            ini.Write("autoReplySimpleMinSnr", MinSnr.ToString());
            ini.Write("autoReplySimpleNewOnly", NewCallsOnly.ToString());
            ini.Write("autoReplySimpleNewDxccOnly", NewDxccOnly.ToString());
            ini.Write("autoReplySimpleNewDxccScope", NewDxccScope.ToString());
            ini.Write("autoReplySimpleExcludeDirCq", ExcludeUnmatchedDirectedCq.ToString());
            ini.Write("autoReplySimpleListMode", ListMode.ToString());
            ini.Write("autoReplySimpleList", ListAsText());
        }
    }
}
