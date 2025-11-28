from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware # 👈 新增這行
import psycopg2
from pydantic import BaseModel
import datetime
import os

app = FastAPI()

# 🔴 記得換返你條 Connection String
DB_URL = "postgresql://postgres.abelbiqlhnvfmksvhdnw:hotprojec20251126@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"

# 👇 新增這段 CORS 設定 (這是解決問題的關鍵！)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # "*" 代表允許所有網址連線 (包括你的本地 index.html)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 定義前端傳過來的訂單格式
class OrderSchema(BaseModel):
    store_name: str
    product_id: int
    unit_id: int
    quantity: int
    
# 定義新增用戶的格式
class UserSchema(BaseModel):
    username: str
    password: str
    display_name: str
    
# 定義登入資料格式
class LoginSchema(BaseModel):
    username: str
    password: str

# 定義修改密碼格式
class ChangePasswordSchema(BaseModel):
    user_id: int
    new_password: str

# 定義入貨/庫存調整格式
class RestockSchema(BaseModel):
    product_id: int
    quantity: float  # 可以係正數 (入貨) 或 負數 (盤點扣除)
    note: str        # 備註 (例如: 供應商入貨 / 盤點損耗)

# 定義產品格式
class CreateProductSchema(BaseModel):
    name: str
    sku: str
    base_unit: str # 例如 KG, L, 個

# 定義產品單位格式
class CreateUnitSchema(BaseModel):
    product_id: int
    unit_name: str      # 例如: 箱
    conversion_rate: float # 例如: 20

@app.get("/")
def home():
    return {"message": "豪大大系統"}

# 1. 查詢庫存 API
@app.get("/products")
def get_products():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.name, p.current_stock, p.base_unit, u.unit_name, u.conversion_rate, u.id
        FROM products p
        JOIN product_units u ON p.id = u.product_id
    """)
    rows = cursor.fetchall()
    results = []
    for row in rows:
        results.append({
            "product_name": row[1],
            "stock_left": f"{row[2]} {row[3]}",
            "selling_unit": row[4],
            "rate": float(row[5]),
            "unit_id": row[6]
        })
    cursor.close()
    conn.close()
    return results

# 2. 下單 API
@app.post("/order")
def create_order(order: OrderSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        # A. 查換算率
        cursor.execute("SELECT conversion_rate FROM product_units WHERE id = %s", (order.unit_id,))
        unit_data = cursor.fetchone()
        if not unit_data:
            raise HTTPException(status_code=400, detail="搵唔到呢個單位 ID")
        rate = float(unit_data[0])
        
        # B. 計算總扣除量
        total_deduct_qty = order.quantity * rate
        
        # C. 檢查庫存
        cursor.execute("SELECT current_stock FROM products WHERE id = %s", (order.product_id,))
        product_data = cursor.fetchone()
        current_stock = float(product_data[0])
        
        if current_stock < total_deduct_qty:
             raise HTTPException(status_code=400, detail=f"庫存不足！只剩 {current_stock}")

        # D. 做數
        order_no = f"ORD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor.execute(
            "INSERT INTO orders (order_number, store_name, status) VALUES (%s, %s, 'APPROVED') RETURNING id",
            (order_no, order.store_name)
        )
        new_order_id = cursor.fetchone()[0]
        
        cursor.execute(
            "INSERT INTO order_items (order_id, product_id, unit_id, quantity, calculated_qty) VALUES (%s, %s, %s, %s, %s)",
            (new_order_id, order.product_id, order.unit_id, order.quantity, total_deduct_qty)
        )
        
        cursor.execute(
            "UPDATE products SET current_stock = current_stock - %s WHERE id = %s",
            (total_deduct_qty, order.product_id)
        )
        
        conn.commit()
        return {
            "status": "success",
            "message": f"成功下單！已扣除 {total_deduct_qty} KG",
            "remaining_stock": current_stock - total_deduct_qty
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
# 3. 後台查詢訂單 API (新增功能)
@app.get("/orders")
def get_orders():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # 用 SQL Join 將訂單、商品、單位連埋一齊查
    query = """
        SELECT 
            o.order_number, 
            o.store_name, 
            to_char(o.order_date, 'YYYY-MM-DD HH24:MI') as order_time,
            p.name as product_name, 
            oi.quantity, 
            u.unit_name,
            oi.calculated_qty
        FROM orders o
        JOIN order_items oi ON o.id = oi.order_id
        JOIN products p ON oi.product_id = p.id
        JOIN product_units u ON oi.unit_id = u.id
        ORDER BY o.order_date DESC;
    """
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        results.append({
            "order_no": row[0],
            "store": row[1],
            "time": row[2],
            "product": row[3],
            "qty": f"{row[4]} {row[5]}",   # 例如: 5 箱
            "total_weight": f"{row[6]} KG" # 例如: 100 KG
        })
    
    cursor.close()
    conn.close()
    return results
    
# 4. Admin 新增用戶 API
@app.post("/create_user")
def create_user(user: UserSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    try:
        # 檢查帳號是否已存在
        cursor.execute("SELECT id FROM store_users WHERE username = %s", (user.username,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="這個帳號 ID 已經有人用了！")

        # 插入新用戶
        cursor.execute(
            "INSERT INTO store_users (username, password, display_name) VALUES (%s, %s, %s)",
            (user.username, user.password, user.display_name)
        )

        conn.commit()
        return {"status": "success", "message": f"成功建立用戶: {user.display_name}"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
# 5. 獲取用戶列表 (已修改：移除密碼欄位，改為回傳 is_reset_needed)
@app.get("/users")
def get_users():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    # 👇 拿走 password，改拿 is_reset_needed
    cursor.execute("SELECT id, username, display_name, is_active, to_char(created_at, 'YYYY-MM-DD'), is_reset_needed FROM store_users ORDER BY id ASC")
    rows = cursor.fetchall()
    
    users = []
    for row in rows:
        users.append({
            "id": row[0],
            "username": row[1],
            "display_name": row[2],
            "is_active": row[3],
            "created_at": row[4],
            "is_reset_needed": row[5] # True 代表下次要改密碼
        })
    cursor.close()
    conn.close()
    return users

# 6. 切換用戶狀態 (停用/啟用)
@app.put("/users/{user_id}/toggle")
def toggle_user_status(user_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        # SQL: 將 is_active 變成相反 (NOT is_active)
        cursor.execute("UPDATE store_users SET is_active = NOT is_active WHERE id = %s RETURNING display_name, is_active", (user_id,))
        result = cursor.fetchone()
        conn.commit()
        
        status_text = "已啟用" if result[1] else "已停用"
        return {"status": "success", "message": f"用戶 {result[0]} {status_text}"}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
# 7. 重置密碼 API (新增功能)
@app.put("/users/{user_id}/reset_password")
def reset_password(user_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        # 預設重置密碼為 "123456"
        default_pwd = "123456"
        
        # SQL: 修改密碼，並設定 is_reset_needed = TRUE
        cursor.execute(
            "UPDATE store_users SET password = %s, is_reset_needed = TRUE WHERE id = %s RETURNING display_name", 
            (default_pwd, user_id)
        )
        result = cursor.fetchone()
        conn.commit()
        
        return {"status": "success", "message": f"已重置 {result[0]} 的密碼為 {default_pwd}"}
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
        
# 8. 分店登入 API
@app.post("/login")
def login(data: LoginSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    try:
        # 查詢用戶
        cursor.execute(
            "SELECT id, display_name, password, is_active, is_reset_needed FROM store_users WHERE username = %s", 
            (data.username,)
        )
        user = cursor.fetchone()
        
        # 1. 檢查帳號是否存在
        if not user:
            raise HTTPException(status_code=401, detail="帳號不存在")
        
        # 2. 檢查密碼 (注意：這裡暫時用明文比對，生產環境建議加密)
        db_password = user[2]
        if db_password != data.password:
            raise HTTPException(status_code=401, detail="密碼錯誤")
            
        # 3. 檢查是否被停用
        is_active = user[3]
        if not is_active:
            raise HTTPException(status_code=403, detail="此帳號已被停用，請聯絡總部")

        # 登入成功，回傳用戶資料
        return {
            "status": "success",
            "user": {
                "id": user[0],
                "display_name": user[1],
                "is_reset_needed": user[4] # 告訴前端是否需要強制改密碼
            }
        }

    finally:
        cursor.close()
        conn.close()

# 9. 用戶修改密碼 API
@app.post("/change_password")
def change_password(data: ChangePasswordSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        # 更新密碼，並將 is_reset_needed 設為 False
        cursor.execute(
            "UPDATE store_users SET password = %s, is_reset_needed = FALSE WHERE id = %s",
            (data.new_password, data.user_id)
        )
        conn.commit()
        return {"status": "success", "message": "密碼修改成功，請重新登入"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# 10. (Admin專用) 獲取商品總庫存列表
@app.get("/admin/products")
def get_admin_products():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    # 直接查 products 表，唔需要 Join 單位表
    cursor.execute("SELECT id, name, sku, current_stock, base_unit, is_active FROM products ORDER BY id ASC")
    rows = cursor.fetchall()
    
    products = []
    for row in rows:
        products.append({
            "id": row[0],
            "name": row[1],
            "sku": row[2],
            "current_stock": row[3],
            "base_unit": row[4],
            "is_active": row[5]
        })
    cursor.close()
    conn.close()
    return products

# 11. 庫存調整/入貨 API (已升級：會寫入 Log 表)
@app.post("/restock")
def restock_product(data: RestockSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        # 1. 更新產品總庫存
        cursor.execute(
            "UPDATE products SET current_stock = current_stock + %s WHERE id = %s RETURNING name, current_stock, base_unit",
            (data.quantity, data.product_id)
        )
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="搵唔到件貨")
        
        # 2. 插入庫存變動紀錄 (新增這一步)
        cursor.execute(
            "INSERT INTO inventory_logs (product_id, change_qty, note) VALUES (%s, %s, %s)",
            (data.product_id, data.quantity, data.note)
        )

        conn.commit()
        
        product_name = result[0]
        new_stock = result[1]
        unit = result[2]
        action = "入貨" if data.quantity > 0 else "扣除"
        
        return {
            "status": "success", 
            "message": f"[{product_name}] 成功{action} {abs(data.quantity)} {unit}。最新庫存: {new_stock} {unit}"
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# 12. 獲取庫存變動紀錄 (支援月份篩選)
@app.get("/admin/inventory_logs")
def get_inventory_logs(month: str = None): 
    # month 格式預期為 "2025-11"
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    sql = """
        SELECT 
            to_char(l.created_at, 'YYYY-MM-DD HH24:MI') as log_time,
            p.name,
            l.change_qty,
            p.base_unit,
            l.note
        FROM inventory_logs l
        JOIN products p ON l.product_id = p.id
    """
    
    # 如果有傳月份過來，就加 Filter
    if month:
        sql += f" WHERE to_char(l.created_at, 'YYYY-MM') = '{month}'"
    
    sql += " ORDER BY l.created_at DESC"
    
    cursor.execute(sql)
    rows = cursor.fetchall()
    
    logs = []
    for row in rows:
        logs.append({
            "time": row[0],
            "product": row[1],
            "qty": float(row[2]),
            "unit": row[3],
            "note": row[4]
        })
    
    cursor.close()
    conn.close()
    return logs

# ==========================
# 產品管理 API (Admin)
# ==========================

# 13. 新增產品 (基礎資料)
@app.post("/admin/products/create")
def create_product(data: CreateProductSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO products (name, sku, base_unit) VALUES (%s, %s, %s) RETURNING id, name",
            (data.name, data.sku, data.base_unit)
        )
        new_prod = cursor.fetchone()

        # 👇 寫入日誌
        cursor.execute(
            "INSERT INTO product_config_logs (product_name, action_type, details) VALUES (%s, %s, %s)",
            (new_prod[1], "新增產品", f"建立新商品 SKU: {data.sku}, 基準單位: {data.base_unit}")
        )

        conn.commit()
        return {"status": "success", "message": f"成功新增產品: {new_prod[1]}"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# 14. 產品上下架 (切換狀態)
@app.put("/admin/products/{product_id}/toggle")
def toggle_product(product_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE products SET is_active = NOT is_active WHERE id = %s RETURNING name, is_active", (product_id,))
        res = cursor.fetchone()
        status = "上架" if res[1] else "下架"

        # 👇 寫入日誌
        cursor.execute(
            "INSERT INTO product_config_logs (product_name, action_type, details) VALUES (%s, %s, %s)",
            (res[0], "狀態變更", f"將商品狀態更改為: {status}")
        )

        conn.commit()
        return {"status": "success", "message": f"[{res[0]}] 已{status}"}
    finally:
        cursor.close()
        conn.close()

# 15. 獲取某產品的所有單位 (用於編輯)
@app.get("/admin/products/{product_id}/units")
def get_product_units(product_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT id, unit_name, conversion_rate FROM product_units WHERE product_id = %s ORDER BY conversion_rate DESC", (product_id,))
    rows = cursor.fetchall()
    units = []
    for row in rows:
        units.append({"id": row[0], "name": row[1], "rate": float(row[2])})
    cursor.close()
    conn.close()
    return units

# 16. 新增單位 (例如為雞胸加一個「箱」的單位)
@app.post("/admin/units/create")
def create_unit(data: CreateUnitSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO product_units (product_id, unit_name, conversion_rate) VALUES (%s, %s, %s)",
            (data.product_id, data.unit_name, data.conversion_rate)
        )

        # 👇 寫入日誌
        cursor.execute(
            "INSERT INTO product_config_logs (product_name, action_type, details) VALUES (%s, %s, %s)",
            (prod[0], "新增單位", f"新增銷售單位: {data.unit_name} (1{data.unit_name} = {data.conversion_rate}{prod[1]})")
        )

        conn.commit()
        return {"status": "success", "message": "成功新增單位"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# 17. 刪除單位
@app.delete("/admin/units/{unit_id}")
def delete_unit(unit_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM product_units WHERE id = %s", (unit_id,))

        # 👇 寫入日誌
        cursor.execute(
            "INSERT INTO product_config_logs (product_name, action_type, details) VALUES (%s, %s, %s)",
            (info[0], "刪除單位", f"刪除了單位: {info[1]}")
        )

        conn.commit()
        return {"status": "success", "message": "已刪除單位"}
    except Exception as e:
        conn.rollback() # 可能是因為有訂單關聯，刪唔到
        raise HTTPException(status_code=400, detail="刪除失敗，可能已有訂單使用此單位")
    finally:
        cursor.close()
        conn.close()

# 18. 獲取產品配置日誌
@app.get("/admin/product_logs")
def get_product_logs():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT to_char(created_at, 'YYYY-MM-DD HH24:MI'), product_name, action_type, details FROM product_config_logs ORDER BY created_at DESC LIMIT 50")
    rows = cursor.fetchall()
    
    logs = []
    for row in rows:
        logs.append({
            "time": row[0],
            "product": row[1],
            "action": row[2],
            "details": row[3]
        })
    cursor.close()
    conn.close()
    return logs