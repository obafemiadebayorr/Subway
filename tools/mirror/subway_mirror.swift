// subway-mirror: find the iPhone Mirroring window and post mouse/keyboard events (roadmap 70).
// Usage: window | trusted | click X Y | drag X1 Y1 X2 Y2 [MS] | scroll DY [DX] | type TEXT | key CODE [cmd,shift,alt,ctrl] Env SUBWAY_MIRROR_RAISE=1: raise the window instead of focusing it in place.
// Prints one JSON line. Screen points, top-left origin.
import Cocoa
import CoreGraphics
import ApplicationServices

func out(_ o: [String: Any]) {
    if let d = try? JSONSerialization.data(withJSONObject: o), let s = String(data: d, encoding: .utf8) { print(s) }
}
func fail(_ m: String) -> Never { out(["error": m]); exit(1) }
func num(_ v: Any?) -> Double { (v as? NSNumber)?.doubleValue ?? 0 }

func findWindow() -> [String: Any]? {
    guard let list = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return nil }
    var best: [String: Any]? = nil
    var bestScore = -1.0
    for w in list {
        let owner = w[kCGWindowOwnerName as String] as? String ?? ""
        let layer = (w[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0
        guard owner == "iPhone Mirroring", layer == 0, let b = w[kCGWindowBounds as String] as? [String: Any] else { continue }
        let wd = num(b["Width"]), ht = num(b["Height"])
        if wd < 150 || ht < 300 { continue }
        let ratio = ht / wd
        // The phone view is a tall portrait window (about 2.2:1); landscape apps give a wide one (about 1:2.2).
        let phoneish = (ratio > 1.6 || ratio < 0.62) ? 1.0 : 0.0
        let onscreen = (w[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue ?? false
        let score = phoneish * 1_000_000 + (onscreen ? 100_000 : 0) + wd * ht / 100
        if score > bestScore {
            bestScore = score
            best = ["id": (w[kCGWindowNumber as String] as? NSNumber)?.intValue ?? 0, "x": num(b["X"]), "y": num(b["Y"]),
                    "w": wd, "h": ht, "onscreen": onscreen, "phone": phoneish > 0]
        }
    }
    if let b = best, (b["phone"] as? Bool) == true { return b }
    return nil
}

let ourMark: Int64 = 0x5355_4257   // "SUBW": events the helper made
var userPid: pid_t = 0              // app the user was in; their keys go back there
var guardTap: CFMachPort? = nil

// Agent waits (never the user): if the user typed in the last 0.8 s, hold off up to 3 s.
func yieldToUser() {
    var waited = 0.0
    while waited < 3.0 && CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .keyDown) < 0.8 {
        usleep(100_000); waited += 0.1
    }
}

// While the helper acts, re-route the user's real keystrokes to the app they were in.
func startKeyGuard() {
    guard userPid != 0 else { return }
    let mask = (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.keyUp.rawValue) | (1 << CGEventType.flagsChanged.rawValue)
    let cb: CGEventTapCallBack = { _, type, event, _ in
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let t = guardTap { CGEvent.tapEnable(tap: t, enable: true) }
            return Unmanaged.passUnretained(event)
        }
        if event.getIntegerValueField(.eventSourceUserData) == ourMark { return Unmanaged.passUnretained(event) }
        event.postToPid(userPid)
        return nil
    }
    guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .defaultTap,
                                      eventsOfInterest: CGEventMask(mask), callback: cb, userInfo: nil) else { return }
    guardTap = tap
    let src = CFMachPortCreateRunLoopSource(nil, tap, 0)
    let ready = DispatchSemaphore(value: 0)
    Thread {
        CFRunLoopAddSource(CFRunLoopGetCurrent(), src, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        ready.signal()
        CFRunLoopRun()
    }.start()
    _ = ready.wait(timeout: .now() + 0.5)
}

// Hand focus straight back and keep re-routing a moment longer for keys already in flight.
func finishAction() {
    guard targetPid != 0 else { return }
    usleep(300_000) // let Mirroring consume the events before it loses key status
    if userPid != 0, let app = NSRunningApplication(processIdentifier: userPid) { app.activate(options: []) }
    usleep(120_000)
    if let t = guardTap { CGEvent.tapEnable(tap: t, enable: false) }
}

// Focus without raise (roadmap 70): make the Mirroring window key through SkyLight so macOS does not switch
// desktops, then send events straight to its process. SUBWAY_MIRROR_RAISE=1 keeps the old global path.
var targetPid: pid_t = 0
typealias SLPostRec = @convention(c) (UnsafeMutablePointer<ProcessSerialNumber>, UnsafeMutablePointer<UInt8>) -> CGError
typealias SLSetFront = @convention(c) (UnsafeMutablePointer<ProcessSerialNumber>, UInt32, UInt32) -> CGError
typealias GetPSN = @convention(c) (pid_t, UnsafeMutablePointer<ProcessSerialNumber>) -> OSStatus

func focusNoRaise() {
    if ProcessInfo.processInfo.environment["SUBWAY_MIRROR_RAISE"] == "1" { return }
    guard let w = findWindow(), let wid0 = w["id"] as? Int,
          let app = NSWorkspace.shared.runningApplications.first(where: { $0.localizedName == "iPhone Mirroring" }),
          let sl = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_NOW),
          let pRec = dlsym(sl, "SLPSPostEventRecordTo"), let pFront = dlsym(sl, "_SLPSSetFrontProcessWithOptions"),
          let pPsn = dlsym(dlopen(nil, RTLD_NOW), "GetProcessForPID") else { return }
    if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != app.processIdentifier {
        userPid = front.processIdentifier
    }
    yieldToUser()
    startKeyGuard()
    let postRec = unsafeBitCast(pRec, to: SLPostRec.self)
    let setFront = unsafeBitCast(pFront, to: SLSetFront.self)
    let getPsn = unsafeBitCast(pPsn, to: GetPSN.self)
    var psn = ProcessSerialNumber()
    guard getPsn(app.processIdentifier, &psn) == 0 else { return }
    var wid = UInt32(wid0)
    _ = setFront(&psn, wid, 0x200)
    for kind: UInt8 in [1, 2] {
        var b = [UInt8](repeating: 0, count: 0xf8)
        b[0x04] = 0xf8; b[0x08] = kind; b[0x3a] = 0x10
        withUnsafeBytes(of: &wid) { for i in 0..<4 { b[0x3c + i] = $0[i] } }
        for i in 0..<0x10 { b[0x20 + i] = 0xff }
        _ = postRec(&psn, &b)
    }
    usleep(80_000)
    targetPid = app.processIdentifier
}

func post(_ e: CGEvent?) {
    e?.setIntegerValueField(.eventSourceUserData, value: ourMark)
    if targetPid != 0 { e?.postToPid(targetPid) } else { e?.post(tap: .cghidEventTap) }
}
func mouse(_ t: CGEventType, _ x: Double, _ y: Double) {
    post(CGEvent(mouseEventSource: nil, mouseType: t, mouseCursorPosition: CGPoint(x: x, y: y), mouseButton: .left))
}

let a = CommandLine.arguments
guard a.count >= 2 else { fail("no command") }
let d = { (i: Int) -> Double in a.count > i ? (Double(a[i]) ?? 0) : 0 }
if ["click", "drag", "scroll", "type", "key"].contains(a[1]) { focusNoRaise(); atexit { finishAction() } }
switch a[1] {
case "window":
    if let w = findWindow() { out(w) } else { out(["error": "iPhone Mirroring window not found"]) }
case "trusted":
    out(["trusted": AXIsProcessTrusted()])
case "click":
    mouse(.mouseMoved, d(2), d(3)); usleep(30_000)
    mouse(.leftMouseDown, d(2), d(3)); usleep(50_000)
    mouse(.leftMouseUp, d(2), d(3))
    out(["ok": true])
case "drag":
    let (x1, y1, x2, y2) = (d(2), d(3), d(4), d(5))
    let ms = a.count > 6 ? max(50.0, d(6)) : 300.0
    let steps = 24
    mouse(.mouseMoved, x1, y1); usleep(30_000)
    mouse(.leftMouseDown, x1, y1); usleep(40_000)
    for i in 1...steps {
        let t = Double(i) / Double(steps)
        mouse(.leftMouseDragged, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        usleep(useconds_t(ms * 1000 / Double(steps)))
    }
    mouse(.leftMouseUp, x2, y2)
    out(["ok": true])
case "scroll":
    let dy = Int32(d(2)), dx = Int32(d(3))
    post(CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0))
    out(["ok": true])
case "type":
    let text = a.count > 2 ? a[2...].joined(separator: " ") : ""
    let plain: [Character: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
        "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
        "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
        "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
        ".": 47, " ": 49, "`": 50, "\n": 36, "\t": 48]
    let shifted: [Character: Character] = [
        "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
        "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";", "\"": "'", "<": ",", ">": ".", "?": "/", "~": "`"]
    var typed = 0
    var skipped: [String] = []
    for ch in text {
        var base = ch
        var shift = false
        if let s = shifted[ch] { base = s; shift = true }
        else if ch.isUppercase, let low = ch.lowercased().first, plain[low] != nil { base = low; shift = true }
        guard let code = plain[base] else { skipped.append(String(ch)); continue }
        let dn = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
        if shift { dn?.flags = .maskShift; up?.flags = .maskShift }
        post(dn); usleep(15_000); post(up); usleep(35_000)
        typed += 1
    }
    var res: [String: Any] = ["ok": true, "typed": typed]
    if !skipped.isEmpty { res["skipped"] = skipped.joined() }
    out(res)
case "key":
    let code = CGKeyCode(d(2))
    var flags = CGEventFlags()
    if a.count > 3 {
        for m in a[3].split(separator: ",") {
            switch m { case "cmd": flags.insert(.maskCommand); case "shift": flags.insert(.maskShift)
            case "alt": flags.insert(.maskAlternate); case "ctrl": flags.insert(.maskControl); default: break }
        }
    }
    let dn = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true); dn?.flags = flags; post(dn)
    usleep(30_000)
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false); up?.flags = flags; post(up)
    out(["ok": true])
case "frontpid":
    let app = NSWorkspace.shared.frontmostApplication
    out(["ok": true, "pid": Int(app?.processIdentifier ?? 0), "name": app?.localizedName ?? ""])
case "activate":
    guard let app = NSRunningApplication(processIdentifier: pid_t(d(2))) else { fail("no such app") }
    app.activate(options: [])
    out(["ok": true])
default:
    fail("unknown command \(a[1])")
}
