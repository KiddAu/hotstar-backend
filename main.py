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