param([switch]$SelfTest, [switch]$AutoEmbed)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System;
using System.IO;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using System.Drawing;
using System.Collections.Generic;
public class WeChatProbe : Form {
 [DllImport("user32.dll", SetLastError=true)] static extern IntPtr SetParent(IntPtr w, IntPtr p);
 [DllImport("user32.dll")] static extern IntPtr GetParent(IntPtr w);
 [DllImport("user32.dll", EntryPoint="GetWindowLongPtrW")] static extern IntPtr GetStyle(IntPtr w,int n);
 [DllImport("user32.dll", EntryPoint="SetWindowLongPtrW",SetLastError=true)] static extern IntPtr SetStyle(IntPtr w,int n,IntPtr v);
 [DllImport("user32.dll")] static extern bool IsWindow(IntPtr w);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr w);
 [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr w,out RECT r);
 [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr w,IntPtr after,int x,int y,int width,int height,uint flags);
 [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr w,int n);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr w,out uint pid);
 delegate bool EnumProc(IntPtr w,IntPtr l);
 [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb,IntPtr l);
 [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr w,uint cmd);
 [StructLayout(LayoutKind.Sequential)] struct RECT {public int L,T,R,B;}
 class Item { public IntPtr Handle; public int Pid; public override string ToString(){return "Weixin PID "+Pid+" / HWND "+Handle;} }
 TextBox path=new TextBox(); ComboBox list=new ComboBox(); Panel host=new Panel(); Label status=new Label();
 IntPtr target=IntPtr.Zero, originalParent, originalStyle; RECT originalRect;
 string root; bool auto; public WeChatProbe(string folder,bool autoEmbed) {
  root=folder; auto=autoEmbed; Text="微信嵌入可行性原型 · 临时验证"; Size=new Size(1400,900); MinimumSize=new Size(1000,650);
  var top=new FlowLayoutPanel {Dock=DockStyle.Top,Height=100,Padding=new Padding(8),WrapContents=true};
  path.Width=500; path.Text=@"C:\Program Files\Tencent\Weixin\Weixin.exe";
  string cfg=Path.Combine(root,"wechat-path.txt"); if(File.Exists(cfg)) path.Text=File.ReadAllText(cfg).Trim();
  top.Controls.Add(new Label {Text="官方微信路径",AutoSize=true,Padding=new Padding(0,7,0,0)}); top.Controls.Add(path);
  AddButton(top,"浏览",delegate {using(var d=new OpenFileDialog {Filter="微信程序|*.exe"}){if(d.ShowDialog()==DialogResult.OK){path.Text=d.FileName;File.WriteAllText(cfg,path.Text);}}});
  AddButton(top,"保存路径",delegate {File.WriteAllText(cfg,path.Text.Trim().Trim('"'));SetStatus("路径已保存");});
  AddButton(top,"启动微信",delegate {string p=path.Text.Trim().Trim('"');if(!File.Exists(p))throw new Exception("路径不存在");Process.Start(p);SetStatus("已请求启动；登录后刷新窗口。");});
  list.Width=350; list.DropDownStyle=ComboBoxStyle.DropDownList;top.Controls.Add(list);
  AddButton(top,"刷新窗口",delegate {RefreshWindows();});
  AddButton(top,"嵌入选中窗口",delegate {var i=list.SelectedItem as Item;if(i==null)throw new Exception("请先选择微信窗口");Embed(i.Handle);});
  AddButton(top,"恢复独立窗口",delegate {Restore();});
  Controls.Add(top);
  var side=new Panel {Dock=DockStyle.Right,Width=270,Padding=new Padding(15),BackColor=Color.FromArgb(242,246,250)};
  side.Controls.Add(new Label {Dock=DockStyle.Fill,Text="分析面板占位\n\n本原型仅验证窗口嵌入。\n没有读取或保存聊天内容，也没有调用模型。\n\n请检查：\n• 中文输入法和正常聊天\n• 调整软件大小\n• 图片、文件、菜单弹窗\n• 恢复独立窗口\n\n微信仍是官方进程。\n暂未验证扫码登录和多开。\n\n关闭本原型会尝试恢复微信。\n请正常关闭，避免强制结束。"});
  Controls.Add(side);
  status.Dock=DockStyle.Bottom;status.Height=44;status.Text="等待选择微信窗口";Controls.Add(status);
  host.Dock=DockStyle.Fill;host.BackColor=Color.FromArgb(230,235,240);Controls.Add(host);host.BringToFront();top.BringToFront();
  host.Resize+=delegate {ResizeTarget();};
  FormClosing+=delegate(object s,FormClosingEventArgs e){try{Restore();}catch(Exception ex){e.Cancel=true;MessageBox.Show("恢复失败："+ex.Message);}};
  Shown+=delegate {RefreshWindows();if(auto && list.Items.Count>0) Guard(delegate {Embed(((Item)list.Items[0]).Handle);});};
 }
 void AddButton(Control parent,string title,Action action){var b=new Button {Text=title,AutoSize=true};b.Click+=delegate{Guard(action);};parent.Controls.Add(b);}
 void Guard(Action a){try{a();}catch(Exception ex){SetStatus("失败："+ex.Message);MessageBox.Show(ex.Message,"验证失败");}}
 void SetStatus(string s){status.Text=s;File.WriteAllText(Path.Combine(root,"status.txt"),DateTime.Now.ToString("s")+" "+s);}
 void RefreshWindows(){
  list.Items.Clear();EnumWindows(delegate(IntPtr w,IntPtr l){uint pid;GetWindowThreadProcessId(w,out pid);
   if(!IsWindowVisible(w)||GetWindow(w,4)!=IntPtr.Zero)return true;
   try{var p=Process.GetProcessById((int)pid);if(p.ProcessName.Equals("Weixin",StringComparison.OrdinalIgnoreCase)||p.ProcessName.Equals("WeChat",StringComparison.OrdinalIgnoreCase)){
    RECT r;GetWindowRect(w,out r);if(r.R-r.L>300&&r.B-r.T>300)list.Items.Add(new Item {Handle=w,Pid=(int)pid});}}catch{}return true;
  },IntPtr.Zero);if(list.Items.Count>0)list.SelectedIndex=0;SetStatus("发现 "+list.Items.Count+" 个微信候选窗口");
 }
 public void Embed(IntPtr w){
  Restore();if(!IsWindow(w))throw new Exception("窗口已经失效");
  originalParent=GetParent(w);originalStyle=GetStyle(w,-16);GetWindowRect(w,out originalRect);target=w;
  long style=originalStyle.ToInt64();SetStyle(w,-16,new IntPtr((style & ~0x80000000L & ~0x00C00000L & ~0x00040000L)|0x40000000L));
  SetParent(w,host.Handle);
  if(GetParent(w)!=host.Handle){int err=Marshal.GetLastWin32Error();Restore();throw new Exception("SetParent 未成功，Win32="+err);}
  ShowWindow(w,5);ResizeTarget();
  SetStatus("嵌入成功（父窗口关系已核验） HWND="+w+"；聊天、输入法与弹窗请实际检查。");
 }
 void ResizeTarget(){if(target!=IntPtr.Zero&&IsWindow(target))SetWindowPos(target,IntPtr.Zero,0,0,Math.Max(1,host.ClientSize.Width),Math.Max(1,host.ClientSize.Height),0x0034);}
 public void Restore(){
  if(target==IntPtr.Zero)return;var w=target;
  if(IsWindow(w)){
   SetStyle(w,-16,originalStyle);
   SetParent(w,originalParent);
   if(GetParent(w)!=originalParent)throw new Exception("无法恢复父窗口，Win32="+Marshal.GetLastWin32Error()+" actual="+GetParent(w)+" expected="+originalParent);
   SetWindowPos(w,IntPtr.Zero,originalRect.L,originalRect.T,originalRect.R-originalRect.L,originalRect.B-originalRect.T,0x0034);
   if(GetStyle(w,-16)!=originalStyle)throw new Exception("无法恢复窗口样式");
   ShowWindow(w,5);
  }
  target=IntPtr.Zero;SetStatus("已恢复独立窗口；父窗口与样式已核验");
 }
 public static void Test(string root){
  using(var main=new WeChatProbe(root,false))using(var mock=new Form()){
   mock.Text="模拟微信窗口";mock.Show();main.Show();Application.DoEvents();
   IntPtr w=mock.Handle;main.Embed(w);main.Size=new Size(1100,700);Application.DoEvents();main.Restore();
   if(GetParent(w)!=IntPtr.Zero)throw new Exception("restore assertion failed");
   File.WriteAllText(Path.Combine(root,"selftest.txt"),"PASS: simulated window attach, resize, restore parent and style. Same-process mock only; not proof of WeChat compatibility.");
   mock.Close();main.Close();
  }
 }
}
'@
if ($SelfTest) { [WeChatProbe]::Test($PSScriptRoot); Get-Content -LiteralPath "$PSScriptRoot\selftest.txt"; exit }
[System.Windows.Forms.Application]::EnableVisualStyles()
$probe = [WeChatProbe]::new($PSScriptRoot, $AutoEmbed.IsPresent)
try { [System.Windows.Forms.Application]::Run($probe) } finally { $probe.Restore() }
