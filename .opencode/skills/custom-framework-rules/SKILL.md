---
name: "custom-framework-rules"
description: "Quy tắc viết mã nguồn đặc thù cho các framework và kiến trúc sử dụng trong dự án này."
---

# Custom Framework Rules

> Quy tắc viết mã nguồn đặc thù cho các framework và kiến trúc sử dụng trong dự án này.

## Quy tắc Thiết kế & Kiến trúc

- **Separation of Concerns:** Tách biệt rõ ràng giữa logic giao diện (UI), logic nghiệp vụ (business logic/services) và truy xuất dữ liệu (DB/API).
- **Dependency Injection:** Sử dụng dependency injection (hoặc patterns tương đương của framework) để dễ dàng viết unit test.
- **Dữ liệu không thay đổi (Immutability):** Ưu tiên sử dụng hằng số (`const` trong JS/TS, `final` trong Java, hoặc immutable objects) để tránh lỗi bất đồng bộ.

## Quy ước Đặt tên (Naming Conventions)

- **Biến và Hàm:** Sử dụng `camelCase` (ví dụ: `getUserData`, `isLogged`).
- **Lớp và Type/Interface:** Sử dụng `PascalCase` (ví dụ: `UserSession`, `DatabaseConfig`).
- **Thư mục và File mã nguồn:** Sử dụng `kebab-case` (ví dụ: `user-controller.ts`, `data-source.js`).

## Xử lý lỗi (Error Handling)

- Không bao giờ nuốt lỗi (silent catch).
- Luôn ghi log lỗi chi tiết kèm ngữ cảnh bằng công cụ logger của hệ thống.
- Trả về mã lỗi thân thiện với người dùng (user-friendly error message) và mã lỗi kỹ thuật rõ ràng để debug.

```typescript
// GOOD
try {
  return await db.users.findUnique({ where: { id } });
} catch (error) {
  logger.error("Failed to fetch user from database", { userId: id, error });
  throw new AppError(ErrorCode.DATABASE_ERROR, "Không thể lấy thông tin người dùng.");
}

// BAD
try {
  return await db.users.findUnique({ where: { id } });
} catch (error) {
  // Silent catch
}
```
