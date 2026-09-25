param([string]$Title = 'Select folder', [string]$Start = '')
# Modern Explorer-style folder picker (IFileOpenDialog with FOS_PICKFOLDERS). Prints the chosen path, or nothing if cancelled.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FolderPicker {
  [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialogRCW {}
  [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItem {
    void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
    void GetParent(out IShellItem ppsi);
    void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
    void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
    void Compare(IShellItem psi, uint hint, out int piOrder);
  }
  [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IFileOpenDialog {
    [PreserveSig] int Show(IntPtr hwndOwner);
    void SetFileTypes(uint c, IntPtr rgFilterSpec);
    void SetFileTypeIndex(uint i);
    void GetFileTypeIndex(out uint i);
    void Advise(IntPtr pfde, out uint cookie);
    void Unadvise(uint cookie);
    void SetOptions(uint fos);
    void GetOptions(out uint fos);
    void SetDefaultFolder(IShellItem psi);
    void SetFolder(IShellItem psi);
    void GetFolder(out IShellItem ppsi);
    void GetCurrentSelection(out IShellItem ppsi);
    void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
    void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
    void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
    void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
    void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
    void GetResult(out IShellItem ppsi);
  }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, ref Guid riid, out IShellItem item);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();

  public static string Pick(string title, string start) {
    var dlg = (IFileOpenDialog)new FileOpenDialogRCW();
    dlg.SetOptions(0x20 | 0x40 | 0x800 | 0x8);   // PICKFOLDERS | FORCEFILESYSTEM | PATHMUSTEXIST | NOCHANGEDIR
    dlg.SetTitle(title);
    if (!string.IsNullOrEmpty(start) && System.IO.Directory.Exists(start)) {
      var iid = new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"); IShellItem si;
      SHCreateItemFromParsingName(start, IntPtr.Zero, ref iid, out si); dlg.SetFolder(si);
    }
    int hr = dlg.Show(GetForegroundWindow());
    if (hr == unchecked((int)0x800704C7)) return "";       // user cancelled
    if (hr != 0) Marshal.ThrowExceptionForHR(hr);
    IShellItem item; dlg.GetResult(out item);
    string path; item.GetDisplayName(0x80058000, out path);  // SIGDN_FILESYSPATH
    return path;
  }
}
'@
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[FolderPicker]::Pick($Title, $Start)
