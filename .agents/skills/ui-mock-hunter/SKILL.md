---
name: ui-mock-hunter
description: Truy tìm và loại bỏ các dữ liệu fix cứng (hardcode) hoặc mock data trên giao diện UI để đảm bảo kết nối API thực.
---

# UI Mock Hunter (Máy Quét Dữ Liệu Giả)

> **MỤC TIÊU**: Đảm bảo mọi luồng dữ liệu trên Frontend đều là dữ liệu thực (Real Data) từ API hoặc State Management, không được để lọt dữ liệu giả.

Là một AI Agent, đôi khi bạn có xu hướng code nhanh giao diện và sử dụng "Hardcoded Data" (ví dụ: `John Doe`, `$99.99`, `lorem ipsum`). Kỹ năng này bắt buộc bạn phải hành xử như một "Thợ săn Mock data".

## Quy tắc quét (Hunt Rules)

Khi bạn tham gia xây dựng hoặc review một file component (Ví dụ: `.tsx`, `.jsx`, `.vue`):
1. **Quét Blacklist:** Dùng mắt hoặc công cụ grep tìm kiếm các cụm từ sau:
   - `Lorem ipsum`
   - `John Doe`, `Jane Doe`
   - `test@example.com`
   - `TODO: fetch from API`
   - Bất kỳ text tĩnh nào giống như mock (ví dụ thẻ cứng `$199.00` trong giỏ hàng).
2. **Quét Cấu trúc:** Nếu một mảng dữ liệu (Array) được khai báo tĩnh ngay trong thân component thay vì lấy từ props hoặc Redux/Context/API, đây là **MOCK DATA**.

## Hành động bắt buộc

Nếu bạn phát hiện Mock Data:
1. **Không được bỏ qua:** Bạn không được phép "để đó tính sau" trừ khi user có yêu cầu "Hãy cứ dùng mock data".
2. **Phản hồi ngay lập tức:** Báo cáo cho user biết "Tôi phát hiện Mock Data tại line X của file Y. Bạn có muốn tôi thay thế nó bằng một API call hay truyền qua Props không?".
3. **Biến thành TODO:** Đổi dữ liệu giả thành một lời gọi hàm TODO, ví dụ:
   `const data = await fetchRealData(); // TODO: implement this`

## Ví dụ:
**❌ Vi phạm (Hardcode):**
```tsx
const UserProfile = () => {
  return (
    <div>
      <h1>John Doe</h1>
      <p>admin@example.com</p>
    </div>
  );
}
```

**✅ Tuân thủ (Data Driven):**
```tsx
const UserProfile = ({ user }) => {
  return (
    <div>
      <h1>{user.name}</h1>
      <p>{user.email}</p>
    </div>
  );
}
```
Mọi hành vi vi phạm sẽ bị cảnh báo trong quá trình Audit mã nguồn.
