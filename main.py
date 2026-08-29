import json
import os
import datetime
import platform
import webbrowser
import io
from threading import Thread

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.treeview import TreeView, TreeViewLabel
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.image import Image as KivyImage
from kivy.clock import Clock
from kivy.storage.jsonstore import JsonStore
from kivy.metrics import dp, sp
from kivy.core.window import Window

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

DB_DOSYA_ID = '1kPd7pDsDx6JmqwdEkrtMzEguHGeJsre9'
SCOPES = ['https://www.googleapis.com/auth/drive']

class YuklemePenceresi(Popup):
    def __init__(self, **kwargs):
        super(YuklemePenceresi, self).__init__(**kwargs)
        self.title = "BİLGİ"
        self.size_hint = (0.8, 0.3)
        self.auto_dismiss = False
        self.content = Label(text="SENKRONİZE EDİLİYOR...", font_size=sp(16), bold=True)

def hesapla_ilerleme(makineData):
    testTotal, testDone, leafTotal, leafPacked = 0, 0, 0, 0
    donanimlar = makineData.get("donanimlar", {})
    paketleme_durumu = makineData.get("paketleme_durumu", {})
    
    for tam_yol, d_data in donanimlar.items():
        kombObj = d_data.get("kombinasyonlar", {})
        for k_dict in kombObj.values():
            for krit in k_dict.values():
                testTotal += 1
                if krit.get("durum") == "yapildi": testDone += 1
                
        is_islem = "İşlem" in d_data.get("karakter", "")
        is_leaf = True
        for other_yol in donanimlar.keys():
            if other_yol != tam_yol and other_yol.startswith(tam_yol + " ->"):
                is_leaf = False
                break
                
        if is_leaf and not is_islem:
            leafTotal += 1
            if tam_yol in paketleme_durumu: leafPacked += 1
            
    pTest = int((testDone/testTotal)*100) if testTotal > 0 else 100
    pSevk = int((leafPacked/leafTotal)*100) if leafTotal > 0 else 100
    if testTotal == 0 and leafTotal == 0: pTest, pSevk = 0, 0
    return pTest, pSevk

def node_durum_hesapla(hedef_yol, donanimlar):
    toplam = 0
    tamamlanan = 0
    
    for y, d in donanimlar.items():
        if y == hedef_yol or y.startswith(hedef_yol + " ->"):
            for k_dict in d.get("kombinasyonlar", {}).values():
                for krit in k_dict.values():
                    toplam += 1
                    if krit.get("durum") == "yapildi":
                        tamamlanan += 1

    if toplam == 0: return "9E9E9E", True
    elif tamamlanan == 0: return "F44336", False
    elif tamamlanan < toplam: return "FF9800", False
    else: return "4CAF50", True

def yetkili_mi(tam_yol, kullanici, db, donanimlar):
    d_data = donanimlar.get(tam_yol, {})
    for key in ["yetkililer", "yetkili", "personeller", "test_personeli"]:
        val = d_data.get(key, [])
        if isinstance(val, list) and kullanici in val: return True
        if isinstance(val, str) and kullanici in val: return True
        
    global_yetki_dictler = [
        db.get("yetkiler", {}), 
        db.get("yetkilendirme", {}),
        db.get("personel_yetkileri", {}),
        db.get("ayarlar", {}).get("yetkiler", {}),
        db.get("ayarlar", {}).get("yetkilendirme", {})
    ]
    
    for yetki_dict in global_yetki_dictler:
        if not isinstance(yetki_dict, dict): continue
        if tam_yol in yetki_dict:
            val = yetki_dict[tam_yol]
            if isinstance(val, list) and kullanici in val: return True
            if isinstance(val, str) and kullanici in val: return True
        if kullanici in yetki_dict:
            val = yetki_dict[kullanici]
            if isinstance(val, list) and tam_yol in val: return True
            
        node_ad = tam_yol.split(" -> ")[-1]
        if node_ad in yetki_dict:
            val = yetki_dict[node_ad]
            if isinstance(val, list) and kullanici in val: return True
            if isinstance(val, str) and kullanici in val: return True
        if kullanici in yetki_dict:
            val = yetki_dict[kullanici]
            if isinstance(val, list) and node_ad in val: return True
            
    return False

def filtrele_tree(tree_dict, donanimlar, rol, kullanici, db):
    yeni_tree = {}
    for k, v in tree_dict.items():
        tam_yol = v['_tam_yol']
        d_data = donanimlar.get(tam_yol, {})
        karakter = d_data.get("karakter", "")
        is_islem = "İşlem" in karakter
        
        cocuklar = filtrele_tree(v['_children'], donanimlar, rol, kullanici, db)
        
        goster = False
        if rol in ['yonetici', 'izleyici']:
            goster = True
        elif rol == 'sevkiyatci':
            if not is_islem or cocuklar: 
                goster = True
        elif rol == 'test_personeli':
            if yetkili_mi(tam_yol, kullanici, db, donanimlar) or cocuklar:
                goster = True
                
        if goster:
            yeni_tree[k] = {'_tam_yol': tam_yol, '_data': v['_data'], '_children': cocuklar}
    return yeni_tree

def veri_tabanini_cek():
    try:
        json_yol = "service_account.json"
        if not os.path.exists(json_yol): return None, "Servis anahtarı bulunamadı!"
        creds = service_account.Credentials.from_service_account_file(json_yol, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        request = service.files().get_media(fileId=DB_DOSYA_ID)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: _, done = downloader.next_chunk()
        fh.seek(0)
        return json.loads(fh.read().decode('utf-8')), None
    except Exception as e: return None, str(e)

def veri_tabanina_yaz(db_data):
    try:
        creds = service_account.Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        json_str = json.dumps(db_data, indent=4)
        media = MediaIoBaseUpload(io.BytesIO(json_str.encode('utf-8')), mimetype='application/json', resumable=True)
        service.files().update(fileId=DB_DOSYA_ID, media_body=media).execute()
        return True, None
    except Exception as e: return False, str(e)

def pdf_bul_ve_indir(dosya_yolu):
    try:
        json_yol = "service_account.json"
        creds = service_account.Credentials.from_service_account_file(json_yol, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        
        try:
            with open(json_yol, "r") as f: sa_email = json.load(f).get("client_email", "Robot Hesap")
        except: sa_email = "Robot Hesap"
            
        dosya_adi = dosya_yolu.replace('\\', '/').split('/')[-1]
        base_name = os.path.splitext(dosya_adi)[0]
        safe_name = base_name.replace("'", "\\'")
        
        query = f"name contains '{safe_name}' and trashed=false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        items = results.get('files', [])
        
        if not items: return False, f"'{base_name}' BULUNAMADI!\nYetki verin:\n{sa_email}"
            
        file_id = items[0]['id']
        request = service.files().get_media(fileId=file_id)
        temp_dir = os.path.join(os.path.expanduser('~'), 'Saha_PDF_Temp')
        os.makedirs(temp_dir, exist_ok=True)
        pdf_yol = os.path.join(temp_dir, items[0]['name'])
        
        fh = io.FileIO(pdf_yol, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done: _, done = downloader.next_chunk()
        fh.close()
        return True, pdf_yol
    except Exception as e: return False, str(e)

def build_tree_dict(donanimlar):
    tree = {}
    for yol in sorted(donanimlar.keys()):
        parcalar = yol.split(" -> ")
        curr = tree
        for i, p in enumerate(parcalar):
            if p not in curr:
                curr[p] = {'_tam_yol': " -> ".join(parcalar[:i+1]), '_data': {}, '_children': {}}
            if i == len(parcalar) - 1: curr[p]['_data'] = donanimlar[yol]
            curr = curr[p]['_children']
    return tree

class ExtendedTreeViewLabel(TreeViewLabel):
    tam_yol = ""

class LoginScreen(Screen):
    def __init__(self, **kwargs):
        super(LoginScreen, self).__init__(**kwargs)
        self.store = JsonStore('yerel_ayarlar.json')
        
        # Merkezleme Operasyonu
        main_layout = AnchorLayout(anchor_x='center', anchor_y='center')
        
        layout = BoxLayout(orientation='vertical', padding=dp(30), spacing=dp(20), size_hint=(0.9, None))
        layout.bind(minimum_height=layout.setter('height'))
        
        layout.add_widget(Label(text="Saha Operasyon Merkezi", font_size=sp(24), bold=True, color=(0, 0.84, 1, 1), size_hint_y=None, height=dp(60)))
        
        kayitli_kullanici = self.store.get('son_kullanici')['ad'] if self.store.exists('son_kullanici') else ""
        self.kullanici_input = TextInput(text=kayitli_kullanici, hint_text="Kullanıcı Adı Soyadı", multiline=False, write_tab=False, size_hint_y=None, height=dp(55), font_size=sp(16), padding_y=[dp(15), 0])
        layout.add_widget(self.kullanici_input)
        
        self.sifre_input = TextInput(hint_text="Şifre", password=True, multiline=False, write_tab=False, size_hint_y=None, height=dp(55), font_size=sp(16), padding_y=[dp(15), 0])
        layout.add_widget(self.sifre_input)
        
        self.giris_btn = Button(text="Giriş Yap", size_hint_y=None, height=dp(60), font_size=sp(18), bold=True, background_color=(0.18, 0.49, 0.2, 1))
        self.giris_btn.bind(on_press=self.giris_kontrol_et)
        layout.add_widget(self.giris_btn)
        
        self.mesaj_label = Label(text="", color=(0.95, 0.26, 0.21, 1), size_hint_y=None, height=dp(40), font_size=sp(14))
        layout.add_widget(self.mesaj_label)
        
        main_layout.add_widget(layout)
        self.add_widget(main_layout)
        self.yukleme_popup = YuklemePenceresi()

    def giris_kontrol_et(self, instance):
        k = self.kullanici_input.text.strip()
        s = self.sifre_input.text.strip()
        if not k or not s:
            self.mesaj_label.text = "Bilgileri eksiksiz girin."
            return
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_giris, args=(k, s)).start()

    def _arka_plan_giris(self, k, s):
        db, hata = veri_tabanini_cek()
        if not db:
            Clock.schedule_once(lambda dt: self.hata_goster(hata))
            return
        
        sifreler = db.get("personel_sifreleri", {})
        if not sifreler.get(k) or sifreler[k] != s:
            Clock.schedule_once(lambda dt: self.hata_goster("Kullanıcı bulunamadı veya hatalı şifre."))
            return
            
        if k in db.get("operatorler", []):
            Clock.schedule_once(lambda dt: self.hata_goster("Saha erişim yetkiniz yoktur."))
            return
            
        rol = 'test_personeli'
        if k in db.get("yoneticiler", []): rol = 'yonetici'
        elif k in db.get("sevkiyatcilar", []): rol = 'sevkiyatci'
        elif k in db.get("izleyiciler", []): rol = 'izleyici'
        
        self.store.put('son_kullanici', ad=k)
        App.get_running_app().aktif_kullanici = k
        App.get_running_app().aktif_rol = rol
        App.get_running_app().tum_db = db
        Clock.schedule_once(lambda dt: self.basarili_gecis())

    def hata_goster(self, msj):
        self.yukleme_popup.dismiss()
        self.mesaj_label.text = msj

    def basarili_gecis(self):
        self.yukleme_popup.dismiss()
        self.sifre_input.text = ""
        self.mesaj_label.text = ""
        self.manager.current = 'list_screen'
        self.manager.get_screen('list_screen').makineleri_yukle()

class MachineListScreen(Screen):
    def __init__(self, **kwargs):
        super(MachineListScreen, self).__init__(**kwargs)
        self.yukleme_popup = YuklemePenceresi()
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        ust_bar = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(60), spacing=dp(5))
        ust_bar.add_widget(Label(text="Aktif Görevler", color=(0, 0.84, 1, 1), font_size=sp(18), bold=True, size_hint_x=0.5))
        
        yenile_btn = Button(text="[YENİLE]", size_hint_x=None, width=dp(80), font_size=sp(13), bold=True, background_color=(0, 0.47, 0.8, 1))
        yenile_btn.bind(on_press=self.yenile_tetikle)
        ust_bar.add_widget(yenile_btn)
        
        info_btn = Button(text="[BİLGİ]", size_hint_x=None, width=dp(60), font_size=sp(13), bold=True, background_color=(0.2, 0.2, 0.2, 1))
        info_btn.bind(on_release=self.kunye_goster)
        ust_bar.add_widget(info_btn)
        
        cikis_btn = Button(text="ÇIKIŞ", size_hint_x=None, width=dp(65), font_size=sp(13), bold=True, background_color=(0.8, 0.2, 0.2, 1))
        cikis_btn.bind(on_release=self.cikis_yap)
        ust_bar.add_widget(cikis_btn)
        layout.add_widget(ust_bar)
        
        self.scroll = ScrollView()
        self.grid = GridLayout(cols=1, spacing=dp(10), size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter('height'))
        self.scroll.add_widget(self.grid)
        layout.add_widget(self.scroll)
        self.add_widget(layout)

    def kunye_goster(self, instance):
        content = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(10))
        if os.path.exists('akrep_beyaz.png'):
            logo = KivyImage(source='akrep_beyaz.png', size_hint_y=None, height=dp(120))
            content.add_widget(logo)
            
        content.add_widget(Label(text="Sistem Tasarımı ve Yazılım Mimarisi", color=(0.7, 0.7, 0.7, 1), font_size=sp(14), halign="center", size_hint_y=None, height=dp(30)))
        content.add_widget(Label(text="Ahmet ALKAN", color=(1, 0.84, 0, 1), font_size=sp(20), bold=True, halign="center", size_hint_y=None, height=dp(40)))
        content.add_widget(Label(text="+90 532 152 4116", color=(1, 1, 1, 1), font_size=sp(16), halign="center", size_hint_y=None, height=dp(30)))
        
        kapat_btn = Button(text="KAPAT", size_hint_y=None, height=dp(50), bold=True, background_color=(0.2, 0.2, 0.2, 1))
        content.add_widget(kapat_btn)
        
        popup = Popup(title='Hakkında', content=content, size_hint=(0.9, 0.6), separator_color=(0, 0.84, 1, 1))
        kapat_btn.bind(on_release=popup.dismiss)
        popup.open()

    def cikis_yap(self, instance):
        App.get_running_app().aktif_kullanici = ""
        App.get_running_app().aktif_rol = ""
        self.manager.current = 'login_screen'

    def yenile_tetikle(self, instance):
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_yenile).start()

    def _arka_plan_yenile(self):
        db, hata = veri_tabanini_cek()
        if db: App.get_running_app().tum_db = db
        Clock.schedule_once(lambda dt: self.makineleri_yukle())

    def makineleri_yukle(self):
        self.yukleme_popup.dismiss()
        self.grid.clear_widgets()

        app = App.get_running_app()
        for seriNo, makineData in app.tum_db.get("aktif_gorevler", {}).items():
            if makineData.get("sevkiyat_tamamlandi", False): continue
            
            pTest, pSevk = hesapla_ilerleme(makineData)
            btn = Button(
                text=f"{makineData.get('musteri', 'Bilinmeyen')}\nSeri No: {seriNo}\nTest: %{pTest} | Sevk: %{pSevk}",
                size_hint_y=None, height=dp(100), font_size=sp(16), bold=True,
                background_color=(0.2, 0.2, 0.2, 1), halign="center"
            )
            btn.bind(on_release=lambda instance, s=seriNo: self.detaya_git(s))
            self.grid.add_widget(btn)

    def detaya_git(self, seri_no):
        App.get_running_app().secili_seri_no = seri_no
        self.manager.current = 'detail_screen'
        self.manager.get_screen('detail_screen').detayi_ciz()

class MachineDetailScreen(Screen):
    def __init__(self, **kwargs):
        super(MachineDetailScreen, self).__init__(**kwargs)
        self.yukleme_popup = YuklemePenceresi()
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        ust_bar = BoxLayout(size_hint_y=None, height=dp(60))
        geri_btn = Button(text="< GERİ", size_hint_x=None, width=dp(80), font_size=sp(15), bold=True, background_color=(0.33, 0.33, 0.33, 1))
        geri_btn.bind(on_release=self.geriye_don)
        ust_bar.add_widget(geri_btn)
        self.baslik_label = Label(text="Makine Detayı", color=(0, 0.84, 1, 1), font_size=sp(16), bold=True)
        ust_bar.add_widget(self.baslik_label)
        layout.add_widget(ust_bar)

        info_bar = BoxLayout(size_hint_y=None, height=dp(50))
        self.lbl_ilerleme = Label(text="", color=(1, 0.84, 0, 1), font_size=sp(15), bold=True)
        info_bar.add_widget(self.lbl_ilerleme)
        
        btn_paketler = Button(text="Paket İçerikleri", size_hint_x=0.5, font_size=sp(14), background_color=(0.8, 0.5, 0, 1), bold=True)
        btn_paketler.bind(on_release=self.paketleri_goster)
        info_bar.add_widget(btn_paketler)
        layout.add_widget(info_bar)

        self.scroll = ScrollView()
        self.grid = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter('height'))
        self.scroll.add_widget(self.grid)
        layout.add_widget(self.scroll)
        self.add_widget(layout)

    def geriye_don(self, instance):
        self.manager.current = 'list_screen'

    def pdf_uyarisi_ekstra(self, dosya_yolu):
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_pdf, args=(dosya_yolu,)).start()

    def _arka_plan_pdf(self, dosya_yolu):
        if str(dosya_yolu).startswith("http"):
            Clock.schedule_once(lambda dt: self._pdf_ac(dosya_yolu))
            return
            
        basari, sonuc = pdf_bul_ve_indir(dosya_yolu)
        if basari: Clock.schedule_once(lambda dt: self._pdf_ac(sonuc))
        else: Clock.schedule_once(lambda dt: self.hata_goster_ui(sonuc))

    def _pdf_ac(self, yol):
        self.yukleme_popup.dismiss()
        try:
            if str(yol).startswith("http"): webbrowser.open(yol)
            elif platform.system() == 'Windows': os.startfile(yol)
            elif platform.system() == 'Darwin': os.system(f'open "{yol}"')
            else: os.system(f'xdg-open "{yol}"')
        except Exception: self.hata_goster_ui("Açılamadı!")

    def hata_goster_ui(self, msg):
        self.yukleme_popup.dismiss()
        self.baslik_label.text = f"{msg}"

    def paketleri_goster(self, instance):
        app = App.get_running_app()
        makineData = app.tum_db.get("aktif_gorevler", {}).get(app.secili_seri_no, {})
        paketler = makineData.get("paketler", {})
        durumlar = makineData.get("paket_durumlari", {})
        
        content = BoxLayout(orientation='vertical', spacing=dp(10), padding=dp(10))
        scroll = ScrollView()
        grid = GridLayout(cols=1, spacing=dp(10), size_hint_y=None)
        grid.bind(minimum_height=grid.setter('height'))
        
        if not paketler:
            grid.add_widget(Label(text="Henüz paket oluşturulmamış.", size_hint_y=None, height=dp(40), font_size=sp(14)))
        else:
            for kasa, icerik in paketler.items():
                if not icerik: continue
                durum = "TAMAMLANDI" if durumlar.get(kasa) == "kapandi" else "DEVAM EDİYOR"
                grid.add_widget(Label(text=f"[b]{kasa}[/b] - Durum: {durum}", markup=True, color=(1,0.84,0,1), size_hint_y=None, height=dp(40), font_size=sp(15)))
                for parca in icerik:
                    grid.add_widget(Label(text=f"- {parca}", size_hint_y=None, height=dp(35), font_size=sp(14)))
        
        scroll.add_widget(grid)
        content.add_widget(scroll)
        
        kapat_btn = Button(text="KAPAT", size_hint_y=None, height=dp(50), font_size=sp(16), bold=True, background_color=(0.8, 0.2, 0.2, 1))
        content.add_widget(kapat_btn)
        
        popup = Popup(title='Paket İçerikleri', content=content, size_hint=(0.95, 0.9))
        kapat_btn.bind(on_release=popup.dismiss)
        popup.open()

    def detayi_ciz(self):
        self.grid.clear_widgets()
        app = App.get_running_app()
        makineData = app.tum_db.get("aktif_gorevler", {}).get(app.secili_seri_no, {})
        
        self.baslik_label.text = f"{makineData.get('musteri', '')} / Seri No: {app.secili_seri_no}"
        
        pTest, pSevk = hesapla_ilerleme(makineData)
        self.lbl_ilerleme.text = f"Test: %{pTest} | Sevk: %{pSevk}"

        ekstra = makineData.get("ekstra_resimler", [])
        if not ekstra: ekstra = makineData.get("ekstra_belgeler", [])
        if not ekstra: ekstra = makineData.get("ekstra_pdf", [])
        
        if ekstra:
            self.grid.add_widget(Label(text="[b]--- EKSTRA BELGELER ---[/b]", markup=True, color=(1, 0.84, 0, 1), size_hint_y=None, height=dp(40), font_size=sp(16)))
            for idx, belge in enumerate(ekstra):
                link = belge.get('yol', belge) if isinstance(belge, dict) else belge
                ad = belge.get('ad', f"Ekstra Belge {idx+1}") if isinstance(belge, dict) else f"Ekstra Belge {idx+1}"
                btn = Button(text=f"[TEKNİK RESMİ AÇ] {ad}", size_hint_y=None, height=dp(55), font_size=sp(15), bold=True, background_color=(0, 0.47, 0.8, 1))
                btn.bind(on_release=lambda instance, l=link: self.pdf_uyarisi_ekstra(l))
                self.grid.add_widget(btn)
                
        self.grid.add_widget(Label(text="[b]--- GÖREV AĞACI ---[/b]", markup=True, color=(0, 0.84, 1, 1), size_hint_y=None, height=dp(40), font_size=sp(16)))

        self.tv = TreeView(hide_root=True, indent_level=dp(35), size_hint_y=None)
        self.tv.bind(minimum_height=self.tv.setter('height'))
        self.tv.bind(selected_node=self.node_secildi)
        
        donanimlar = makineData.get("donanimlar", {})
        ham_tree = build_tree_dict(donanimlar)
        
        filtrelenmis_tree = filtrele_tree(ham_tree, donanimlar, app.aktif_rol, app.aktif_kullanici, app.tum_db)
        
        self.add_nodes(self.tv, filtrelenmis_tree, donanimlar, makineData.get("paketleme_durumu", {}), makineData.get("paket_durumlari", {}))
        self.grid.add_widget(self.tv)

    def add_nodes(self, tv, tree_dict, donanimlar, paket_durumu, paket_durumlari, parent=None):
        for k, v in tree_dict.items():
            tam_yol = v['_tam_yol']
            is_islem = "İşlem" in v['_data'].get("karakter", "")
            
            renk_kodu, _ = node_durum_hesapla(tam_yol, donanimlar)
            prefix = "[İŞLEM] " if is_islem else ""
            suffix = ""
            
            if tam_yol in paket_durumu:
                kasa = paket_durumu[tam_yol].get('kasa', '')
                d_str = "TAMAMLANDI" if paket_durumlari.get(kasa) == "kapandi" else "DEVAM EDİYOR"
                suffix = f"   [color=#00BFFF][{kasa} - {d_str}][/color]"
                
            yazi = f"[color=#{renk_kodu}]{prefix}{k}[/color]{suffix}"
            node = ExtendedTreeViewLabel(text=yazi, font_size=sp(15), markup=True, size_hint_y=None, height=dp(60))
            node.tam_yol = tam_yol
            tv.add_node(node, parent)
            if v['_children']: self.add_nodes(tv, v['_children'], donanimlar, paket_durumu, paket_durumlari, node)

    def node_secildi(self, treeview, node):
        if not node or not node.tam_yol: return
        App.get_running_app().secili_donanim_yol = node.tam_yol
        treeview.deselect_node()
        self.manager.current = 'test_screen'
        self.manager.get_screen('test_screen').testleri_ciz()

class TestScreen(Screen):
    def __init__(self, **kwargs):
        super(TestScreen, self).__init__(**kwargs)
        self.yukleme_popup = YuklemePenceresi()
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        
        ust_bar = BoxLayout(size_hint_y=None, height=dp(60))
        geri_btn = Button(text="< GERİ", size_hint_x=None, width=dp(80), font_size=sp(15), bold=True, background_color=(0.33, 0.33, 0.33, 1))
        geri_btn.bind(on_release=self.geriye_don)
        ust_bar.add_widget(geri_btn)
        
        self.baslik_label = Label(text="İşlem Paneli", color=(0, 0.84, 1, 1), font_size=sp(15), bold=True, halign="center")
        ust_bar.add_widget(self.baslik_label)
        layout.add_widget(ust_bar)

        self.scroll = ScrollView()
        self.grid = GridLayout(cols=1, spacing=dp(10), size_hint_y=None, padding=dp(5))
        self.grid.bind(minimum_height=self.grid.setter('height'))
        self.scroll.add_widget(self.grid)
        layout.add_widget(self.scroll)
        self.add_widget(layout)

    def geriye_don(self, instance):
        self.manager.current = 'detail_screen'
        self.manager.get_screen('detail_screen').detayi_ciz()

    def pdf_uyarisi(self, instance):
        self.yukleme_popup.open()
        app = App.get_running_app()
        dosya_yolu = app.tum_db.get("aktif_gorevler", {}).get(app.secili_seri_no, {}).get("donanimlar", {}).get(app.secili_donanim_yol, {}).get("pdf_yolu", "")
        Thread(target=self._arka_plan_pdf, args=(dosya_yolu,)).start()

    def _arka_plan_pdf(self, dosya_yolu):
        if str(dosya_yolu).startswith("http"):
            Clock.schedule_once(lambda dt: self._pdf_ac(dosya_yolu))
            return
            
        basari, sonuc = pdf_bul_ve_indir(dosya_yolu)
        if basari: Clock.schedule_once(lambda dt: self._pdf_ac(sonuc))
        else: Clock.schedule_once(lambda dt: self.hata_goster_ui(sonuc))

    def _pdf_ac(self, yol):
        self.yukleme_popup.dismiss()
        try:
            if str(yol).startswith("http"): webbrowser.open(yol)
            elif platform.system() == 'Windows': os.startfile(yol)
            elif platform.system() == 'Darwin': os.system(f'open "{yol}"')
            else: os.system(f'xdg-open "{yol}"')
        except Exception: self.hata_goster_ui("Açılamadı!")

    def teyit_ac(self, komb, krit, t_obj, input_obj, mn, mx, islem_tipi):
        val = ""
        durum = "yapildi"
        hata = ""
        
        if islem_tipi == "deger":
            val = input_obj.text.strip()
            if not val: return
            try: num = float(val.replace(',', '.'))
            except: return
            if mn != "" and mx != "" and (num < float(mn) or num > float(mx)):
                durum, hata = "revizyon", f"Aralık Dışı ({val})"
        elif islem_tipi == "red":
            durum, hata = "revizyon", "Fiziksel Red"

        tarihce = t_obj.get("tarihce", [])
        mesaj = ""
        
        if tarihce:
            son = tarihce[0]
            eski_durum = "ONAY" if son.get("durum") == "yapildi" else "RED"
            mesaj = f"DİKKAT! Bu işlem daha önce {son.get('tarih')} tarihinde {son.get('yapan')} tarafından {eski_durum} olarak kaydedilmiş.\n\nMevcut kaydın üzerine yazmak istediğinize emin misiniz?"
        else:
            if durum == "revizyon" and islem_tipi == "deger":
                mesaj = f"Girdiğiniz değer ({val}) sınırların dışında!\nBu test RED statüsünde kaydedilecektir.\nOnaylıyor musunuz?"
            else:
                durum_txt = "UYGUN (ONAY)" if durum == "yapildi" else "RED"
                mesaj = f"Bu kriteri {durum_txt} olarak kaydediyorsunuz.\nOnaylıyor musunuz?"

        content = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        lbl = Label(text=mesaj, font_size=sp(14), halign="center", markup=True)
        lbl.bind(size=lambda s, w: setattr(s, 'text_size', (w[0]*0.9, None)))
        content.add_widget(lbl)
        
        btn_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(55), spacing=dp(10))
        btn_evet = Button(text="EVET", background_color=(0, 0.6, 0, 1), font_size=sp(15), bold=True)
        btn_iptal = Button(text="İPTAL", background_color=(0.8, 0, 0, 1), font_size=sp(15), bold=True)
        btn_box.add_widget(btn_evet)
        btn_box.add_widget(btn_iptal)
        content.add_widget(btn_box)

        popup = Popup(title='İşlem Onayı', content=content, size_hint=(0.95, 0.5), auto_dismiss=False)
        btn_iptal.bind(on_release=popup.dismiss)
        btn_evet.bind(on_release=lambda instance: self.islemi_onayla(popup, komb, krit, durum, hata, val))
        popup.open()

    def islemi_onayla(self, popup, komb, krit, durum, hata, val):
        popup.dismiss()
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_test_gonder, args=(komb, krit, durum, hata, val)).start()

    def testleri_ciz(self):
        self.grid.clear_widgets()
        app = App.get_running_app()
        yol = app.secili_donanim_yol
        self.baslik_label.text = yol.split(" -> ")[-1]
        
        makineData = app.tum_db.get("aktif_gorevler", {}).get(app.secili_seri_no, {})
        donanimlar = makineData.get("donanimlar", {})
        donanim_data = donanimlar.get(yol, {})
        
        if donanim_data.get("pdf_yolu"):
            pdf_btn = Button(text="[TEKNİK RESMİ AÇ]", size_hint_y=None, height=dp(55), font_size=sp(15), bold=True, background_color=(0, 0.47, 0.8, 1))
            pdf_btn.bind(on_release=self.pdf_uyarisi)
            self.grid.add_widget(pdf_btn)

        if app.aktif_rol in ['test_personeli', 'yonetici']:
            self.grid.add_widget(Label(text="[b]--- TEST GEÇMİŞİ ---[/b]", markup=True, color=(0, 0.84, 1, 1), size_hint_y=None, height=dp(35), font_size=sp(16)))
            kombinasyonlar = donanim_data.get("kombinasyonlar", {})
            if not kombinasyonlar:
                self.grid.add_widget(Label(text="Kriter yok.", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=dp(30), font_size=sp(14)))
            else:
                for komb, kriterler in kombinasyonlar.items():
                    self.grid.add_widget(Label(text=f"[b]{komb}[/b]", markup=True, color=(1, 0.84, 0, 1), size_hint_y=None, height=dp(35), font_size=sp(15)))
                    for krit, t in kriterler.items():
                        kutu_boyu = dp(200) if app.aktif_rol == 'test_personeli' else dp(140)
                        box = BoxLayout(orientation='vertical', size_hint_y=None, height=kutu_boyu, padding=dp(8), spacing=dp(8))
                        box.add_widget(Label(text=f"{krit}", color=(0.9, 0.9, 0.9, 1), font_size=sp(14), bold=True, size_hint_y=None, height=dp(30)))

                        tarihce = t.get("tarihce", [])
                        if not tarihce and t.get("durum") not in ["", "yapilmadi"]:
                            tarihce = [{"durum": t.get("durum"), "yapan": t.get("yapan", ""), "tarih": t.get("tarih", ""), "girilen_deger": t.get("girilen_deger", ""), "hata_notu": t.get("hata_notu", "")}]

                        if not tarihce:
                            box.add_widget(Label(text="Durum: Bekliyor", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=dp(30), font_size=sp(13)))
                        else:
                            hist_scroll = ScrollView(size_hint_y=None, height=dp(90))
                            hist_grid = GridLayout(cols=1, spacing=dp(3), size_hint_y=None)
                            hist_grid.bind(minimum_height=hist_grid.setter('height'))
                            for gecmis in tarihce:
                                renk = "00FF00" if gecmis['durum'] == "yapildi" else "FF0000"
                                durum_txt = "ONAY" if gecmis['durum'] == "yapildi" else f"RED ({gecmis.get('hata_notu','')})"
                                val_txt = f" [{gecmis.get('girilen_deger')}]" if gecmis.get('girilen_deger') else ""
                                hist_grid.add_widget(Label(text=f"[color={renk}]{gecmis.get('tarih')} | {gecmis.get('yapan')} | {durum_txt}{val_txt}[/color]", markup=True, halign="center", size_hint_y=None, height=dp(30), font_size=sp(13)))
                            hist_scroll.add_widget(hist_grid)
                            box.add_widget(hist_scroll)

                        if app.aktif_rol == 'test_personeli':
                            btn_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(55), spacing=dp(10))
                            if t.get("tip") == "deger":
                                mn, mx = t.get("min", ""), t.get("max", "")
                                ph = f"({mn}-{mx} {t.get('birim', '')})" if mn != "" else "Değer"
                                inp = TextInput(hint_text=ph, input_type='number', multiline=False, font_size=sp(15), padding_y=[dp(15), 0])
                                btn_kaydet = Button(text="KAYDET", background_color=(0, 0.6, 0, 1), bold=True, font_size=sp(14), size_hint_x=0.5)
                                btn_kaydet.bind(on_release=lambda instance, k=komb, kr=krit, t_obj=t, i_obj=inp, min_v=mn, max_v=mx: self.teyit_ac(k, kr, t_obj, i_obj, min_v, max_v, "deger"))
                                btn_box.add_widget(inp)
                                btn_box.add_widget(btn_kaydet)
                            else:
                                btn_uygun = Button(text="UYGUN", background_color=(0, 0.6, 0, 1), font_size=sp(14), bold=True)
                                btn_uygun.bind(on_release=lambda instance, k=komb, kr=krit, t_obj=t: self.teyit_ac(k, kr, t_obj, None, "", "", "uygun"))
                                btn_red = Button(text="RED VER", background_color=(0.8, 0, 0, 1), font_size=sp(14), bold=True)
                                btn_red.bind(on_release=lambda instance, k=komb, kr=krit, t_obj=t: self.teyit_ac(k, kr, t_obj, None, "", "", "red"))
                                btn_box.add_widget(btn_uygun)
                                btn_box.add_widget(btn_red)
                            box.add_widget(btn_box)
                            
                        self.grid.add_widget(box)

        self.grid.add_widget(Label(text="[b]--- LOJİSTİK ---[/b]", markup=True, color=(1, 0.65, 0, 1), size_hint_y=None, height=dp(45), font_size=sp(16)))
        
        is_islem = "İşlem" in donanim_data.get("karakter", "")
        if is_islem:
            self.grid.add_widget(Label(text="Paketlenemez ([İŞLEM]).", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=dp(35), font_size=sp(15)))
        else:
            p_durumu = makineData.get("paketleme_durumu", {}).get(yol)
            if p_durumu:
                kasa = p_durumu.get("kasa", "")
                durum_str = "TAMAMLANDI" if makineData.get("paket_durumlari", {}).get(kasa) == "kapandi" else "DEVAM EDİYOR"
                renk = "FF0000" if durum_str == "TAMAMLANDI" else "00FF00"
                
                self.grid.add_widget(Label(text=f"[PAKET: {kasa}] [color={renk}][{durum_str}][/color]", markup=True, halign="center", size_hint_y=None, height=dp(45), font_size=sp(15)))
                
                if app.aktif_rol == 'sevkiyatci':
                    btn_cikar = Button(text="PAKETTEN ÇIKAR", background_color=(0.8, 0, 0, 1), size_hint_y=None, height=dp(55), font_size=sp(14), bold=True)
                    btn_cikar.bind(on_release=lambda instance, k=kasa: self.sevkiyat_cikar(yol, k))
                    self.grid.add_widget(btn_cikar)
            else:
                if app.aktif_rol == 'sevkiyatci':
                    _, hepsi_yesil_mi = node_durum_hesapla(yol, donanimlar)
                    if not hepsi_yesil_mi:
                        self.grid.add_widget(Label(text="⚠️ Testleri eksik parça paketlenemez!", color=(1, 0, 0, 1), size_hint_y=None, height=dp(45), font_size=sp(15)))
                    else:
                        paket_durumlari = makineData.get("paket_durumlari", {})
                        spinner_vals = []
                        for i in range(1, 51):
                            pk = f"PACKAGE {i}"
                            if pk in paket_durumlari:
                                st = "Tamamlandı" if paket_durumlari[pk] == "kapandi" else "Devam Ediyor"
                                spinner_vals.append(f"{pk} ({st})")
                            else:
                                spinner_vals.append(f"{pk} (Boş)")
                                
                        self.paket_secici = Spinner(
                            text='Paket Seç...', values=spinner_vals,
                            size_hint_y=None, height=dp(60), font_size=sp(15)
                        )
                        self.grid.add_widget(self.paket_secici)
                        
                        btn_box_sevk = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(55), spacing=dp(10))
                        btn_acik = Button(text="ATA & AÇIK BIRAK", background_color=(1, 0.6, 0, 1), font_size=sp(13), bold=True)
                        btn_acik.bind(on_release=lambda instance: self.sevkiyat_ata(yol, "ata_acik"))
                        btn_box_sevk.add_widget(btn_acik)
                        
                        btn_kapat = Button(text="ATA & TAMAMLA", background_color=(0.8, 0, 0, 1), font_size=sp(13), bold=True)
                        btn_kapat.bind(on_release=lambda instance: self.sevkiyat_ata(yol, "ata_kapat"))
                        btn_box_sevk.add_widget(btn_kapat)
                        
                        self.grid.add_widget(btn_box_sevk)
                else:
                    self.grid.add_widget(Label(text="Henüz Paketlenmemiş.", color=(0.7, 0.7, 0.7, 1), size_hint_y=None, height=dp(35), font_size=sp(14)))

    def sevkiyat_ata(self, yol, islem_tipi):
        secim = self.paket_secici.text
        if secim == 'Paket Seç...': return
        kasa = secim.split(" (")[0]
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_sevk, args=(yol, kasa, islem_tipi)).start()

    def sevkiyat_cikar(self, yol, kasa):
        self.yukleme_popup.open()
        Thread(target=self._arka_plan_sevk, args=(yol, kasa, "cikar")).start()

    def _arka_plan_sevk(self, yol, kasa, islem_tipi):
        app = App.get_running_app()
        db, err = veri_tabanini_cek()
        if not db: return

        gorev = db["aktif_gorevler"][app.secili_seri_no]
        if "paketleme_durumu" not in gorev: gorev["paketleme_durumu"] = {}
        if "paketler" not in gorev: gorev["paketler"] = {}
        if "paket_durumlari" not in gorev: gorev["paket_durumlari"] = {}

        t_str = datetime.datetime.now().strftime("%d.%m.%Y - %H:%M")

        if islem_tipi == "cikar":
            if yol in gorev["paketleme_durumu"]: del gorev["paketleme_durumu"][yol]
            if kasa in gorev["paketler"] and yol in gorev["paketler"][kasa]:
                gorev["paketler"][kasa].remove(yol)
                if not gorev["paketler"][kasa]: del gorev["paket_durumlari"][kasa]
                elif gorev["paket_durumlari"].get(kasa) == "kapandi": gorev["paket_durumlari"][kasa] = "devam_eden"
        else:
            gorev["paket_durumlari"][kasa] = "kapandi" if islem_tipi == "ata_kapat" else "devam_eden"
            gorev["paketleme_durumu"][yol] = {"kasa": kasa, "personel": app.aktif_kullanici, "tarih": t_str}
            if kasa not in gorev["paketler"]: gorev["paketler"][kasa] = []
            if yol not in gorev["paketler"][kasa]: gorev["paketler"][kasa].append(yol)

        basari, err2 = veri_tabanina_yaz(db)
        if basari:
            app.tum_db = db
            Clock.schedule_once(lambda dt: self.basarili_gonder_ui())
        else:
            Clock.schedule_once(lambda dt: self.hata_goster_ui("Yazılamadı!"))

    def _arka_plan_test_gonder(self, komb, krit, durum, hata, val):
        app = App.get_running_app()
        db, err = veri_tabanini_cek()
        if not db: return

        tObj = db["aktif_gorevler"][app.secili_seri_no]["donanimlar"][app.secili_donanim_yol]["kombinasyonlar"][komb][krit]
        t_str = datetime.datetime.now().strftime("%d.%m.%Y - %H:%M")

        tObj.update({"durum": durum, "hata_notu": hata, "yapan": app.aktif_kullanici, "girilen_deger": val, "tarih": t_str})
        if "tarihce" not in tObj: tObj["tarihce"] = []
        tObj["tarihce"].insert(0, {"durum": durum, "yapan": app.aktif_kullanici, "tarih": t_str, "hata_notu": hata, "girilen_deger": val, "konum": "Mobil APP"})

        basari, err2 = veri_tabanina_yaz(db)
        if basari:
            app.tum_db = db
            Clock.schedule_once(lambda dt: self.basarili_gonder_ui())
        else: Clock.schedule_once(lambda dt: self.hata_goster_ui("Yazılamadı!"))

    def basarili_gonder_ui(self):
        self.yukleme_popup.dismiss()
        self.testleri_ciz()

    def hata_goster_ui(self, msg):
        self.yukleme_popup.dismiss()
        self.baslik_label.text = f"{msg}"

class SahaMobilApp(App):
    aktif_kullanici = ""
    aktif_rol = ""
    tum_db = {}
    secili_seri_no = ""
    secili_donanim_yol = ""

    def build(self):
        # Klavye kalkanı (Klavye açıldığında ekranı yukarı iter)
        Window.softinput_mode = 'below_target'
        
        sm = ScreenManager()
        sm.add_widget(LoginScreen(name='login_screen'))
        sm.add_widget(MachineListScreen(name='list_screen'))
        sm.add_widget(MachineDetailScreen(name='detail_screen'))
        sm.add_widget(TestScreen(name='test_screen'))
        return sm

if __name__ == '__main__':
    SahaMobilApp().run()