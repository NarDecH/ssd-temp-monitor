// ============================================================================
//  ssd_temp_lite.lpr - SSD Temperature Monitor LITE
//  ----------------------------------------------------------------------------
//  Portable single-file Windows tray app that shows the LIVE temperature of
//  your SSD(s) directly on the system tray icon.
//
//  Language : Object Pascal for Typhon IDE / Lazarus (Free Pascal).
//             Open this .lpr and press Run (F9) - no other unit is needed,
//             everything lives in this one file (that is the point of LITE).
//
//  What it does (and nothing more):
//    1. every 5 seconds it asks Windows for the SSD temperatures using the
//       exact same PowerShell SMART query as the main Python app,
//    2. it paints the hottest temperature as DIGITS on the tray icon with the
//       same color bands (green <= 50 C, orange 51-64 C, red >= 65 C),
//    3. tooltip lists every SSD; menu: Details / Refresh / Exit.
//
//  The SMART query needs an ELEVATED process: right-click the exe and choose
//  "Run as administrator". Without it the icon simply shows "--".
//
//  Command line build (works with Lazarus too):
//      lazbuild ssd_temp_lite.lpr
// ============================================================================

{$mode objfpc}{$H+}                     // {$mode objfpc}: modern Object Pascal
                                        // {$H+}: long AnsiString type
{$ifdef mswindows}{$apptype gui}{$endif} // GUI subsystem: no console window

program ssd_temp_lite;

uses
  {$ifdef unix}cthreads,{$endif}
  Windows, Messages,                    // Win32 API: timer, mutex, menus
  ShellAPI,                             // Shell_NotifyIcon: the tray icon
  SysUtils, Classes,                    // strings, lists, exceptions
  Process,                              // TProcess: spawn powershell.exe
  Math;                                 // Max() used in the details window

const
  // ----------------------------------------------------------- configuration
  POLL_MS = 5000;            // poll every 5 s - LITE is deliberately calm
  ICON_SIZE = 32;            // tray bitmap size; Windows scales as needed
  WM_TRAYCALLBACK = WM_APP + 1;  // our private message for tray events
  MUTEX_NAME = 'Local\SSDTempMonitorLite_SingleInstance';

  // TColor is $00BBGGRR (BGR), so the hex values look swapped vs. CSS.
  COL_GREEN  = $0022C55E;    // same green as the main app
  COL_ORANGE = $000099F5;    // same orange
  COL_RED    = $004444EF;    // same red
  COL_GREY   = $0094A3B4;    // "unknown" grey (no data / not elevated)

  // The SMART query - byte-for-byte the same idea as PS_TEMPS inside
  // ssd_temp_tray.py of the main app:
  //   Get-PhysicalDisk                -> enumerate physical disks
  //   MediaType -eq 'SSD'             -> SSDs only (no HDDs)
  //   BusType -ne 'USB'               -> USB bridges report bogus temps
  //   Get-StorageReliabilityCounter   -> SMART data (Temperature, Wear)
  //   ConvertTo-Json -Compress        -> one parseable JSON line
  PS_TEMPS =
    '$ErrorActionPreference=''SilentlyContinue'';' +
    '$d=Get-PhysicalDisk | Where-Object { $_.MediaType -eq ''SSD'' ' +
    '-and $_.BusType -ne ''USB'' };' +
    '$r=@(); foreach($x in $d){' +
    '$c=$x|Get-StorageReliabilityCounter;' +
    '$r+=[pscustomobject]@{model=$x.Model;temp=$c.Temperature;wear=$c.Wear}};' +
    '$r|ConvertTo-Json -Compress';

resourcestring
  S_TITLE   = 'SSD Temp Lite';
  S_DETAILS = 'Details';
  S_REFRESH = 'Refresh';
  S_EXIT    = 'Exit';
  S_NOADMIN = 'right-click > Run as administrator to read SMART';

// Forward declaration: TTrayApp.Create registers this callback BEFORE the
// function body below is compiled, so it must be announced first.
function LiteWndProc(Wnd: HWND; Msg: UINT; WParam: WPARAM;
  LParam: LPARAM): LRESULT; stdcall; forward;

type
  { TTrayApp: the whole application - one tray icon, one timer, one menu. }
  TTrayApp = class
  private
    // ANSI tray struct (TNotifyIconDataA): szTip holds 64 AnsiChars, which
    // is plenty since we truncate the tooltip. Shell_NotifyIcon + this
    // struct are the exact FPC ShellAPI declarations - no A/W guessing.
    FIconData: ShellAPI.TNotifyIconDataA;
    FMenu: HMENU;                 // Win32 popup menu handle
    FHidden: HWND;                // message-only window: our event receiver
    FTimerId: UINT_PTR;           // SetTimer() id for the poll tick
    FTemp: Integer;               // hottest SSD temp; -1 = unknown
    FModels: TStringList;         // tooltip lines, e.g. "Samsung 970: 37 C"
    FLastIcon: HICON;             // the icon we pushed last (must be freed)
    procedure SetTooltip(const Tip: string);
    function  MakeDigitIcon(const Digits: string; Pill, Digit: TColor): HICON;
    procedure ReadTemps;
    procedure RepaintTray;
    procedure ShowDetails;
    procedure ShowPopupMenu;
    procedure DoExit;
  public
    constructor Create;
    destructor Destroy; override;
    procedure HandleMessage(var Msg: TMessage);
  end;

var
  TrayApp: TTrayApp = nil;

// ============================================================================
//  Minimal JSON field extraction.
//  The PowerShell result is tiny: [{"model":"X","temp":37,"wear":0}, ...]
//  A full JSON parser is overkill; we just walk every "key": occurrence.
// ============================================================================

{ Collect every raw value of "Key": into Values. Handles "string" and 123.
  Returns True when at least one value was found. }
function JsonExtractAll(const Json, Key: string; Values: TStrings): Boolean;
var
  Rest, Pat: string;
  P, V: Integer;
begin
  Result := False;
  Values.Clear;
  Pat := '"' + Key + '":';
  Rest := Json;
  P := Pos(Pat, Rest);
  while P > 0 do
  begin
    Inc(P, Length(Pat));
    while (P <= Length(Rest)) and (Rest[P] = ' ') do
      Inc(P);                                  // skip spaces after the colon
    if (P <= Length(Rest)) and (Rest[P] = '"') then
    begin
      V := P + 1;                              // string value: find closing
      while (V <= Length(Rest)) and (Rest[V] <> '"') do
        Inc(V);
      if V <= Length(Rest) then
      begin
        Values.Add(Copy(Rest, P + 1, V - P - 1));
        Result := True;
      end;
    end
    else
    begin
      V := P;                                  // number / null value
      while (V <= Length(Rest)) and
            (CharInSet(Rest[V], ['0'..'9', '-', '.'])) do
        Inc(V);
      if V > P then
      begin
        Values.Add(Copy(Rest, P, V - P));
        Result := True;
      end;
    end;
    Rest := Copy(Rest, V, MaxInt);             // continue past this value
    P := Pos(Pat, Rest);
  end;
end;

{ "37" -> 37; "null"/garbage -> -1. StrToIntDef never raises. }
function SafeTemp(const Raw: string): Integer;
begin
  if Raw = 'null' then
    Exit(-1);
  Result := StrToIntDef(Trim(Raw), -1);
end;

{ Temperature -> pill color. Same bands as the main Python app. }
function TempColor(const Temp: Integer): TColor;
begin
  if Temp < 0  then Exit(COL_GREY);
  if Temp >= 65 then Exit(COL_RED);
  if Temp >= 51 then Exit(COL_ORANGE);
  Exit(COL_GREEN);
end;

// ============================================================================
//  TTrayApp
// ============================================================================

constructor TTrayApp.Create;
var
  WndClass: TWndClassEx;
  Item: TMenuItemInfo;
begin
  FTemp := -1;
  FModels := TStringList.Create;

  // ---- 1) message-only window --------------------------------------------
  // A message-only window (parent HWND_MESSAGE) is invisible but still
  // receives our tray callbacks and timer events. It keeps the whole app
  // event-driven with exactly ONE thread - no locks anywhere.
  FillChar(WndClass, SizeOf(WndClass), 0);
  WndClass.cbSize        := SizeOf(WndClass);
  WndClass.lpfnWndProc   := @LiteWndProc;      // our callback (forward-declared)
  WndClass.hInstance     := HInstance;
  WndClass.lpszClassName := 'SSDTempLiteHidden';
  Windows.RegisterClassEx(WndClass);
  FHidden := Windows.CreateWindowEx(
    WS_EX_TOOLWINDOW,             // never in the taskbar/alt-tab list
    'SSDTempLiteHidden',          // class name registered above
    'ssd_temp_lite',              // window name (unused)
    0, 0, 0, 0, 0,                // no style, no geometry
    HWND_MESSAGE,                 // <-- message-only parent
    0, HInstance, nil);

  // ---- 2) register the tray slot ------------------------------------------
  FillChar(FIconData, SizeOf(FIconData), 0);
  FIconData.cbSize := SizeOf(FIconData);
  FIconData.Wnd             := FHidden;        // callback receiver
  FIconData.uID             := 1;              // slot id inside this process
  FIconData.uFlags          := NIF_MESSAGE or NIF_ICON or NIF_TIP;
  FIconData.uCallbackMessage := WM_TRAYCALLBACK; // sent on mouse events
  SetTooltip(S_TITLE);                         // initial tooltip
  Shell_NotifyIcon(NIM_ADD, @FIconData);       // create the tray icon

  // ---- 3) popup menu: Details / Refresh / (separator) / Exit --------------
  // TMenuItemInfo + InsertMenuItem are the ANSI variants (FPC Windows unit
  // aliases); our captions are plain ASCII so ANSI is safe here.
  FMenu := CreatePopupMenu;
  FillChar(Item, SizeOf(Item), 0);
  Item.cbSize  := SizeOf(Item);
  Item.fMask   := MIIM_ID or MIIM_STRING;      // we set id + caption
  Item.wID     := 1;
  Item.dwTypeData := PAnsiChar(S_DETAILS);
  InsertMenuItem(FMenu, 0, True, Item);
  Item.wID     := 2;
  Item.dwTypeData := PAnsiChar(S_REFRESH);
  InsertMenuItem(FMenu, 1, True, Item);
  Item.wID     := 0;                           // separator: id only
  Item.dwTypeData := nil;
  Item.fType   := MFT_SEPARATOR;
  InsertMenuItem(FMenu, 2, True, Item);
  Item.fType   := 0;                           // back to a normal item
  Item.wID     := 3;
  Item.dwTypeData := PAnsiChar(S_EXIT);
  InsertMenuItem(FMenu, 3, True, Item);

  // ---- 4) poll timer -------------------------------------------------------
  // SetTimer posts WM_TIMER to the window every POLL_MS: the SMART query
  // therefore runs on the SAME thread as the GUI - simple and safe.
  FTimerId := SetTimer(FHidden, 1, POLL_MS, nil);

  ReadTemps;                                   // first paint right away
end;

destructor TTrayApp.Destroy;
begin
  KillTimer(FHidden, FTimerId);                // stop the poll timer
  Shell_NotifyIcon(NIM_DELETE, @FIconData);    // remove the tray icon
  if FLastIcon <> 0 then
    DestroyIcon(FLastIcon);                    // release the last digits icon
  FreeAndNil(FModels);
  inherited Destroy;
end;

{ Copy a Pascal string into the ANSI szTip field safely:
  zero the buffer first (so it is always #0-terminated), then move at
  most 60 characters - the NOTIFYICONDATAA tooltip holds 64 AnsiChars. }
procedure TTrayApp.SetTooltip(const Tip: string);
var
  A: AnsiString;
begin
  FillChar(FIconData.szTip, SizeOf(FIconData.szTip), 0);
  A := AnsiString(Copy(Tip, 1, 60));
  if Length(A) > 0 then
    Move(PAnsiChar(A)^, FIconData.szTip, Length(A));
end;

{ Draw the temperature as digits on a colored pill - raw GDI, no LCL.
  Returns a fresh HICON; the caller owns it (we free the previous one). }
function TTrayApp.MakeDigitIcon(const Digits: string;
  Pill, Digit: TColor): HICON;
var
  ScreenDC, MemDC: HDC;
  Bmp, OldBmp, Mask, OldMask: HBITMAP;
  Brush: HBRUSH;
  Font, OldFont: HFONT;
  R: TRect;
  Info: TIconInfo;
  WDigits, WFont: UnicodeString;   // DrawTextW/CreateFontW take PWideChar
begin
  Result := 0;
  ScreenDC := GetDC(0);                        // screen DC: color format ref
  try
    MemDC := CreateCompatibleDC(ScreenDC);     // memory DC to draw into
    Bmp   := CreateCompatibleBitmap(ScreenDC, ICON_SIZE, ICON_SIZE);
    Mask  := CreateBitmap(ICON_SIZE, ICON_SIZE, 1, 1, nil); // opaque mask
    OldBmp := SelectObject(MemDC, Bmp);
    try
      // 1) the colored pill = the whole 32x32 square
      Brush := CreateSolidBrush(Pill);
      R.Left := 0; R.Top := 0; R.Right := ICON_SIZE; R.Bottom := ICON_SIZE;
      FillRect(MemDC, R, Brush);
      DeleteObject(Brush);
      // 2) bold Segoe UI digits, centered, no background box
      WDigits := UTF8Decode(Digits);           // AnsiString -> UnicodeString
      WFont   := UTF8Decode('Segoe UI');
      Font := CreateFontW(
        IfThen(Length(Digits) >= 3, -18, -22), // smaller when 3 digits
        0, 0, 0, FW_BOLD, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH, PWideChar(WFont));
      OldFont := SelectObject(MemDC, Font);
      SetBkMode(MemDC, TRANSPARENT);
      SetTextColor(MemDC, Digit);
      DrawTextW(MemDC, PWideChar(WDigits), -1, R,
                DT_CENTER or DT_VCENTER or DT_SINGLELINE);
      SelectObject(MemDC, OldFont);
      DeleteObject(Font);
    finally
      SelectObject(MemDC, OldBmp);
    end;
    DeleteDC(MemDC);
    // 3) color bitmap + mask -> real HICON
    FillChar(Info, SizeOf(Info), 0);
    Info.fIcon    := True;
    Info.hbmColor := Bmp;
    Info.hbmMask  := Mask;
    Result := CreateIconIndirect(Info);
  finally
    ReleaseDC(0, ScreenDC);
    DeleteObject(Bmp);
    DeleteObject(Mask);
  end;
end;

{ Run PowerShell, parse JSON, remember the hottest SSD temperature. }
procedure TTrayApp.ReadTemps;
var
  Proc: TProcess;
  Output: TStringList;
  Temps, Models: TStrings;
  Json: string;
  I, T, Hottest: Integer;
begin
  Hottest := -1;
  FModels.Clear;
  Output := TStringList.Create;
  Temps := TStringList.Create;
  Models := TStringList.Create;
  Proc := TProcess.Create(nil);
  try
    try
      Proc.Executable := 'powershell.exe';
      // -NoProfile  : skip user profile scripts (much faster startup)
      // -NonInteractive : never prompt
      // -Command    : run our one-liner
      Proc.Parameters.Add('-NoProfile');
      Proc.Parameters.Add('-NonInteractive');
      Proc.Parameters.Add('-Command');
      Proc.Parameters.Add(PS_TEMPS);
      Proc.Options := Proc.Options + [poUsePipes, poNoConsole, poWaitOnExit];
      Proc.Execute;                            // runs and waits (poWaitOnExit)
      Output.LoadFromStream(Proc.Output);      // read stdout
      Json := Trim(Output.Text);

      JsonExtractAll(Json, 'temp', Temps);     // ["37","41",...]
      JsonExtractAll(Json, 'model', Models);   // ["X","Y",...]
      for I := 0 to Temps.Count - 1 do
      begin
        T := SafeTemp(Temps[I]);
        if (I < Models.Count) and (Models[I] <> '') then
          FModels.Add(Format('%s: %d C', [Models[I], T]))
        else
          FModels.Add(Format('SSD %d: %d C', [I + 1, T]));
        if T > Hottest then
          Hottest := T;                        // hottest SSD drives the icon
      end;
    except
      Hottest := -1;                           // PowerShell missing or failed
    end;
  finally
    Proc.Free;
    Output.Free;
    Temps.Free;
    Models.Free;
  end;

  FTemp := Hottest;
  if FTemp < 0 then
    FModels.Insert(0, S_NOADMIN);              // explain the "--" state
  RepaintTray;
end;

{ Rebuild the digits icon and push it into the tray slot. }
procedure TTrayApp.RepaintTray;
var
  NewIcon: HICON;
  Tip: string;
  Digits: string;
  Pill: TColor;
begin
  Digits := '--';
  Pill   := COL_GREY;
  if FTemp >= 0 then
  begin
    Digits := IntToStr(FTemp);                 // e.g. "37"
    Pill   := TempColor(FTemp);                // colored pill background
  end;

  NewIcon := MakeDigitIcon(Digits, Pill, clBlack);
  if NewIcon = 0 then
    Exit;                                      // GDI failed: keep old icon
  if FLastIcon <> 0 then
    DestroyIcon(FLastIcon);                    // free the previous one
  FLastIcon      := NewIcon;
  FIconData.hIcon := NewIcon;
  // "line1 | line2" - truncated to 60 chars inside SetTooltip
  Tip := StringReplace(FModels.Text, sLineBreak, ' | ', [rfReplaceAll]);
  SetTooltip(Tip);
  Shell_NotifyIcon(NIM_MODIFY, @FIconData);    // update icon + tooltip
end;

{ Details as a native MessageBox: zero forms, zero LCL, and it renders
  Unicode model names correctly. Runs on the GUI thread. }
procedure TTrayApp.ShowDetails;
var
  Body, WBody, WTitle: UnicodeString;
begin
  if FModels.Count = 0 then
    Body := 'No SSD data (admin required?)'
  else
    Body := FModels.Text;                      // one line per SSD
  WBody  := UTF8Decode(Body);                  // -> real Unicode for *W APIs
  WTitle := UTF8Decode(S_TITLE + ' - Details');
  MessageBoxW(FHidden, PWideChar(WBody), PWideChar(WTitle),
              MB_OK or MB_ICONINFORMATION);
end;

{ Build the visible menu loop: TrackPopupMenuEx returns the chosen id. }
procedure TTrayApp.ShowPopupMenu;
var
  Pt: TPoint;
  Cmd: Integer;
begin
  GetCursorPos(Pt);                            // open at the mouse
  SetForegroundWindow(FHidden);                // required: menu must close
  Cmd := TrackPopupMenuEx(FMenu,
    TPM_RIGHTALIGN or TPM_BOTTOMALIGN or TPM_RETURNCMD,  // anchored above
    Pt.X, Pt.Y, FHidden, nil);                 // TPM_RETURNCMD: no WM_COMMAND
  PostMessage(FHidden, WM_NULL, 0, 0);         // swallow the trailing click
  case Cmd of
    1: ShowDetails;
    2: ReadTemps;
    3: DoExit;
  end;
end;

{ Post WM_QUIT: the message loop in the program block then ends cleanly. }
procedure TTrayApp.DoExit;
begin
  PostMessage(FHidden, WM_QUIT, 0, 0);
end;

{ Central dispatcher for every message of the hidden window. }
procedure TTrayApp.HandleMessage(var Msg: TMessage);
begin
  case Msg.Msg of
    WM_TRAYCALLBACK:                          // mouse on the tray icon
      case Msg.LParam of
        WM_LBUTTONUP: ShowDetails;            // left click  -> details
        WM_RBUTTONUP: ShowPopupMenu;          // right click -> menu
      end;
    WM_TIMER:                                 // the 5 s poll tick
      if THandle(Msg.WParam) = FTimerId then
        ReadTemps;
  end;
  Msg.Result := Windows.DefWindowProc(FHidden, Msg.Msg,
                                      Msg.WParam, Msg.LParam);
end;

// Thunk: the Win32 API calls a plain function, we forward to the object.
// (Declared forward near the top so TTrayApp.Create can reference it.)
function LiteWndProc(Wnd: HWND; Msg: UINT; WParam: WPARAM;
  LParam: LPARAM): LRESULT; stdcall;
var
  M: TMessage;
begin
  if TrayApp = nil then
    Exit(Windows.DefWindowProc(Wnd, Msg, WParam, LParam));
  M.Msg    := Msg;
  M.WParam := WParam;
  M.LParam := LParam;
  M.Result := 0;
  TrayApp.HandleMessage(M);
  Exit(M.Result);
end;

{ Single instance guard: ERROR_ALREADY_EXISTS means someone else owns it. }
function AnotherInstanceRunning: Boolean;
var
  M: THandle;
begin
  M := CreateMutexW(nil, False, MUTEX_NAME);
  Result := (M <> 0) and (GetLastError = ERROR_ALREADY_EXISTS);
  // the handle is intentionally never released: it lives for the process
end;

// ============================================================================
//  Program entry: guard -> start -> pump messages until WM_QUIT.
// ============================================================================
var
  Msg: TMsg;
begin
  if AnotherInstanceRunning then
    Exit;                                      // one lite instance at a time

  TrayApp := TTrayApp.Create;

  // Classic Win32 message pump: dispatches WM_TIMER + tray callbacks and
  // returns 0 when the Exit item posts WM_QUIT.
  while GetMessage(Msg, 0, 0, 0) do
  begin
    TranslateMessage(Msg);                     // keyboard messages (hygiene)
    DispatchMessage(Msg);                      // -> LiteWndProc
  end;

  FreeAndNil(TrayApp);
end.
