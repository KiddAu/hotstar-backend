from fastapi import FastAPI, HTTPException
import psycopg2
from pydantic import BaseModel
import datetime

app = FastAPI()

# 🔴 記得換返你條 Connection String
DB_URL = "postgresql://postgres.abelbiqlhnvfmksvhdnw:hotprojec20251126@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"

# 定義前端傳過來的訂單格式
# 我們新增了 unit_id (用來分辨係買箱定係買包)
class OrderSchema(BaseModel):
    store_name: str
    product_id: int
    unit_id: int   # 例如: 1=箱, 2=包
    quantity: int  # 例如: 5

@app.get("/")
def home():
    return {"message": "豪大大系統 API - 準備就緒"}

# 1. 查詢庫存 API
@app.get("/products")
def get_products():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    # 我們同時查詢埋 units (單位表)，方便睇有咩單位揀
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
            "stock_left": f"{row[2]} {row[3]}", # 顯示 5000 KG
            "selling_unit": row[4],             # 顯示 箱
            "rate": float(row[5]),              # 顯示 20
            "unit_id": row[6]                   # 單位ID
        })
    cursor.close()
    conn.close()
    return results

# 2. 下單 API (核心邏輯！)
@app.post("/order")
def create_order(order: OrderSchema):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    try:
        # A. 查換算率：先睇下客揀個單位，1件等於幾多KG？
        cursor.execute("SELECT conversion_rate FROM product_units WHERE id = %s", (order.unit_id,))
        unit_data = cursor.fetchone()
        
        if not unit_data:
            raise HTTPException(status_code=400, detail="搵唔到呢個單位 ID")
            
        rate = float(unit_data[0]) # 例如 20.0
        
        # B. 計算總扣除量
        total_deduct_qty = order.quantity * rate # 5箱 * 20 = 100KG
        
        # C. 檢查庫存夠唔夠
        cursor.execute("SELECT current_stock, name FROM products WHERE id = %s", (order.product_id,))
        product_data = cursor.fetchone()
        current_stock = float(product_data[0])
        product_name = product_data[1]
        
        if current_stock < total_deduct_qty:
             raise HTTPException(status_code=400, detail=f"庫存不足！只剩 {current_stock} KG")

        # D. 開始做數 (Transaction)
        # 1. 插入訂單紀錄
        order_no = f"ORD-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor.execute(
            "INSERT INTO orders (order_number, store_name, status) VALUES (%s, %s, 'APPROVED') RETURNING id",
            (order_no, order.store_name)
        )
        new_order_id = cursor.fetchone()[0]
        
        # 2. 插入明細
        cursor.execute(
            "INSERT INTO order_items (order_id, product_id, unit_id, quantity, calculated_qty) VALUES (%s, %s, %s, %s, %s)",
            (new_order_id, order.product_id, order.unit_id, order.quantity, total_deduct_qty)
        )
        
        # 3. 最重要：扣減庫存
        cursor.execute(
            "UPDATE products SET current_stock = current_stock - %s WHERE id = %s",
            (total_deduct_qty, order.product_id)
        )
        
        # 確認交易 (Commit)
        conn.commit()
        
        return {
            "status": "success",
            "message": f"成功下單！已扣除 {total_deduct_qty} KG",
            "remaining_stock": current_stock - total_deduct_qty
        }

    except Exception as e:
        conn.rollback() # 有錯就還原
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()